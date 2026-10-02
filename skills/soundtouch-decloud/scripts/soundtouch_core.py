"""Shared logic for talking to a Bose SoundTouch speaker.

Only the standard library, so these modules import in a bare environment. That is also why the
records below are frozen dataclasses and StrEnums rather than pydantic models: each reply from the
speaker is parsed ONCE into one of them, and turned back into a plain dict only by its `to_json`,
at the moment a script prints its envelope.

The parsing here looks simpler than it is, and each function documents the reading that a plausible
implementation gets wrong: a configuration value sits on the line AFTER its field name, the source
entries are self-closing tags whose status is an attribute rather than a label, and the telnet
writes have an order in which the persisting command must come last.
"""

from __future__ import annotations

import base64
import html
import ipaddress
import json
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Protocol, cast

TELNET_PORT = 17000
API_PORT = 8090
SSH_PORT = 22
PROMPT = b"->"

# How far a speaker's clock may be out before it is called wrong. A whole DAY, because the reading
# it judges comes from the speaker's own Date header, which renders the box's LOCAL time and then
# labels it GMT - so a perfectly correct clock reads a full UTC offset out, and no offset on earth
# reaches a day. The fault this catches is eleven years, not minutes.
CLOCK_TOLERANCE_S = 86400


class UrlField(StrEnum):
    """The four service URLs a speaker carries, in the order a migrated speaker lists them."""

    MARGE = "margeServerUrl"
    STATS = "statsServerUrl"
    SW_UPDATE = "swUpdateUrl"
    BMX_REGISTRY = "bmxRegistryUrl"


class RadioSource(StrEnum):
    """The radio sources a speaker mounts from the registry, and the only ones this skill judges."""

    TUNEIN = "TUNEIN"
    LOCAL_INTERNET_RADIO = "LOCAL_INTERNET_RADIO"
    RADIO_BROWSER = "RADIO_BROWSER"


class SourceStatus(StrEnum):
    """The source status words this skill acts on.

    A speaker's status is FIRMWARE text and is carried as it was reported (UNAVAILABLE and the
    like pass straight through); only READY is acted on. ABSENT and UNKNOWN are this skill's own
    words, for a source the speaker never listed and for an entry that carried no status.
    """

    READY = "READY"
    ABSENT = "ABSENT"
    UNKNOWN = "?"


class ContentItemType(StrEnum):
    """The ContentItem type this skill writes. Every other type is carried as the speaker stored it."""

    STATION_URL = "stationurl"


class RegistryVerdict(StrEnum):
    OK = "ok"
    FOREIGN = "foreign"
    DNS_MODE = "dns-mode"
    UNREADABLE = "unreadable"
    UNJUDGED = "unjudged"


class ClockVerdict(StrEnum):
    OK = "ok"
    WRONG = "wrong"
    UNKNOWN = "unknown"


class StreamKind(StrEnum):
    """What a candidate stream URL turned out to be.

    `classify_stream` answers the first five from a fetch. MISSING and KEPT are given without one:
    a template hole nobody has filled yet, and an entry that is not radio at all.
    """

    AUDIO = "audio"
    PLAYLIST = "playlist"
    HLS = "hls"
    DEAD = "dead"
    NOT_AUDIO = "not-audio"
    MISSING = "missing"
    KEPT = "kept"


URL_FIELDS = tuple(UrlField)
CLOUD_MARKERS = ("bose.com", "bose.io", "bosecm.com")
RADIO_SOURCES = tuple(RadioSource)
# The legacy host-bound playback form. READ, because speakers keep what was stored and some of
# AfterTouch's own playback paths and catalog entries produce it; never written here.
PLAYBACK_PATH = "/custom/v1/playback/"
# The Orion adapter, where LOCAL_INTERNET_RADIO lives on a BMX host. AfterTouch's registry advertises
# it as that source's baseUrl, "<service>" + ORION_BASE_PATH.
ORION_BASE_PATH = "/core02/svc-bmx-adapter-orion/prod/orion"
# The station endpoint below it. A location starting with this is the RELATIVE form AfterTouch's
# player and CLI write since v0.138.0 and the one this skill writes: the speaker prepends the baseUrl
# its registry gave it, so the preset follows the service to a new address.
ORION_STATION_PATH = "/station"
# The ABSOLUTE form: the same station with a host in front. Still read everywhere, and still plays;
# written only when asked, for firmware that cannot resolve the relative form.
ORION_PATH = ORION_BASE_PATH + ORION_STATION_PATH
# What Go's json.Marshal escapes on top of JSON itself (its HTML-safe default).
_GO_JSON_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
                    " ": "\\u2028", " ": "\\u2029"}
# Served for .m3u and .pls. They are TEXT that lists streams, so a bare `audio/` test passes them
# and the resulting preset is accepted at write time and never plays.
PLAYLIST_TYPES = ("audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl",
                  "audio/x-scpls", "application/pls+xml", "audio/scpls")

# The firmware passes this configuration value to a shell, so a suffix appended to it runs on the
# speaker the next time the value is read. That read is the whole mechanism: see
# build_enable_ssh_commands for why an unpaired speaker never executes it.
SSH_INJECT = ";touch /tmp/remote_services;/etc/init.d/sshd start"

__all__ = [
    "UrlField", "RadioSource", "SourceStatus", "ContentItemType", "RegistryVerdict",
    "ClockVerdict", "StreamKind",
    "ServiceUrls", "RadioSources", "TelnetReply", "RegistryCheck", "ClockState", "PresetItem",
    "Presets", "PresetEntry", "RelativizeStep",
    "URL_FIELDS", "RADIO_SOURCES",
    "parse_urls", "parse_sources", "service_urls",
    "build_url_commands", "build_enable_ssh_commands", "SSH_INJECT", "orion_location", "ORION_PATH",
    "ORION_BASE_PATH", "ORION_STATION_PATH", "relative_orion_location", "relativize_plan",
    "registry_verdict",
    "PLAYBACK_PATH", "decode_playback_location", "slots_to_write", "missing_streams",
    "parse_presets", "port_open", "telnet_run", "http_get",
    "decode_cloud_location", "stream_url_from_location", "is_cloud_location", "harvest_presets",
    "classify_stream", "playlist_targets", "PLAYLIST_TYPES",
    "http_date_header", "clock_state", "CLOCK_TOLERANCE_S",
    "SpeakerError", "json_object", "json_list", "PromptSocket", "read_to_prompt",
]


class SpeakerError(RuntimeError):
    """The speaker did not answer the way its firmware is documented to."""


@dataclass(frozen=True)
class ServiceUrls:
    """Service URLs a speaker carries, in the order they were read; a field may be absent."""

    values: Mapping[UrlField, str]

    def __bool__(self) -> bool:
        return bool(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __contains__(self, field: object) -> bool:
        return field in self.values

    def get(self, field: UrlField) -> str:
        """One field's value, or "" when the speaker did not report it."""
        return self.values.get(field, "")

    def missing(self) -> list[UrlField]:
        """The fields the speaker did not report at all, in canonical order."""
        return [field for field in UrlField if field not in self.values]

    def cloud_leftovers(self) -> ServiceUrls:
        """Fields still pointing at the shut-down Bose cloud.

        All three domains are checked because they are not interchangeable: clearing only bose.com
        leaves bmxRegistryUrl on bose.io, and without that the speaker mounts no radio source at all
        while otherwise looking migrated.
        """
        return ServiceUrls({k: v for k, v in self.values.items() if any(m in v for m in CLOUD_MARKERS)})

    def injected(self) -> ServiceUrls:
        """Fields still carrying shell text from the injection method, which must be cleaned up."""
        return ServiceUrls({k: v for k, v in self.values.items() if ";" in v})

    def to_json(self) -> dict[str, str]:
        return {field.value: value for field, value in self.values.items()}


@dataclass(frozen=True)
class RadioSources:
    """Each radio source's status as the speaker reported it (see SourceStatus)."""

    statuses: Mapping[RadioSource, str]

    def __bool__(self) -> bool:
        return bool(self.statuses)

    def is_ready(self, source: RadioSource) -> bool:
        return self.statuses.get(source) == SourceStatus.READY

    def radio_ready(self) -> bool:
        """Internet radio is usable: LOCAL_INTERNET_RADIO is READY and no source the speaker lists
        is still loading.

        A source the speaker never listed (ABSENT) is not a loading one - firmware 20, the
        Wireless Link Adapter, publishes no RADIO_BROWSER at all - but LOCAL_INTERNET_RADIO is not
        excused, because every preset this skill writes plays through it.
        """
        listed = [status for status in self.statuses.values() if status != SourceStatus.ABSENT]
        return (self.is_ready(RadioSource.LOCAL_INTERNET_RADIO)
                and all(status == SourceStatus.READY for status in listed))

    def to_json(self) -> dict[str, str]:
        return {source.value: str(status) for source, status in self.statuses.items()}


@dataclass(frozen=True)
class TelnetReply:
    """One command sent to the diagnostic port and what came back.

    `complete` is False when the `->` prompt never arrived and the reply is whatever had been
    received when the read timed out.
    """

    cmd: str
    reply: str
    complete: bool

    def to_json(self) -> dict[str, object]:
        return {"cmd": self.cmd, "reply": self.reply, "complete": self.complete}


@dataclass(frozen=True)
class RegistryCheck:
    """What the BMX registry advertises, judged against the service it should name.

    `expected` and `advertised` are None only when the registry could not be fetched at all, in
    which case `error` says why.
    """

    verdict: RegistryVerdict
    expected: str | None = None
    advertised: Mapping[str, str] | None = None
    reason: str = ""
    error: str = ""

    def to_json(self) -> dict[str, object]:
        if self.expected is None:
            return {"verdict": self.verdict.value, "error": self.error}
        out: dict[str, object] = {"expected": self.expected, "advertised": dict(self.advertised or {}),
                                  "verdict": self.verdict.value}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class ClockState:
    """A speaker's clock judged from its Date header, and the wall clock the box itself reported."""

    verdict: ClockVerdict
    reading: str | None

    def to_json(self) -> dict[str, object]:
        return {"verdict": self.verdict.value, "reading": self.reading}


@dataclass(frozen=True)
class PresetItem:
    """One button as the speaker stores it. `name` is "" when the slot carries no itemName."""

    button: int
    location: str
    source: str
    type: str
    name: str


@dataclass(frozen=True)
class Presets:
    """A speaker's /presets, read once.

    `items` holds the buttons that carry a location, keyed by button. `locations` is every stored
    location in document order, which is what counts how many presets a speaker holds.
    """

    items: Mapping[int, PresetItem]
    locations: tuple[str, ...]

    def slots(self) -> dict[int, str]:
        """Which BUTTON currently holds which location."""
        return {button: item.location for button, item in self.items.items()}


@dataclass(frozen=True)
class PresetEntry:
    """One button of a preset template: what the owner wants on it.

    An entry with `keep` set is not radio (an album, a library track); it is carried over exactly
    as stored and never written.
    """

    button_number: int
    name: str
    location: str
    content_item_type: str = ContentItemType.STATION_URL
    source: str = RadioSource.LOCAL_INTERNET_RADIO
    keep: bool = False

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"buttonNumber": self.button_number, "name": self.name,
                                  "location": self.location,
                                  "contentItemType": str(self.content_item_type),
                                  "source": str(self.source)}
        if self.keep:
            out["keep"] = True
        return out


@dataclass(frozen=True)
class RelativizeStep:
    """One host-bound button, and the storePreset body that stores it again in the relative form."""

    button: int
    old: str
    new: str
    body: str


def parse_urls(raw: str) -> ServiceUrls:
    """Read the four service URLs out of `getpdo CurrentSystemConfiguration` output.

    getpdo prints `<name> {` and puts the value on a FOLLOWING line as `text: "..."`. A single-line
    pattern therefore matches the field names and captures no values at all, which reads as a
    successful check against a speaker that was never actually read.
    """
    found: dict[UrlField, str] = {}
    lines = raw.splitlines()
    for idx, line in enumerate(lines):
        for name in UrlField:
            if name in line and "{" in line:
                for follow in lines[idx + 1: idx + 4]:
                    if "text:" in follow:
                        found[name] = follow.split("text:", 1)[1].strip().strip('",')
                        break
    return ServiceUrls(found)


def parse_sources(raw: str) -> RadioSources:
    """Map each radio source to its status, defaulting to ABSENT.

    The entries are SELF-CLOSING tags carrying no text, so a `<tag>Label</tag>` pattern finds
    nothing and reports every source missing. Match the attribute. ABSENT stays distinct from a real
    status so a source the speaker never published cannot read as READY.
    """
    seen: dict[RadioSource, str] = {}
    for chunk in raw.split("<sourceItem")[1:]:
        for name in RadioSource:
            if f'source="{name}"' in chunk:
                seen[name] = (chunk.split('status="', 1)[1].split('"', 1)[0] if 'status="' in chunk
                              else SourceStatus.UNKNOWN)
    return RadioSources({name: seen.get(name, SourceStatus.ABSENT) for name in RadioSource})


def service_urls(service: str) -> ServiceUrls:
    """The four URLs a migrated speaker must carry."""
    service = service.rstrip("/")
    return ServiceUrls({
        UrlField.MARGE: service,
        UrlField.STATS: service,
        UrlField.SW_UPDATE: f"{service}/updates/soundtouch",
        UrlField.BMX_REGISTRY: f"{service}/bmx/registry/v1/services",
    })


def build_url_commands(service: str, *, inject: str = "") -> list[str]:
    """The telnet sequence that points a speaker at the local service.

    Two rules are encoded here, neither discoverable from the replies. All four fields go through
    `sys configuration`, because `envswitch boseurls set` accepts only the account and update URLs;
    omit them and bmxRegistryUrl is never written, so the speaker syncs presets and plays nothing.
    And `envswitch` comes LAST, because it SAVES the current runtime state: in the other order every
    value is gone after the reboot even though each command answered OK.
    """
    wanted = service_urls(service)
    marge = wanted.get(UrlField.MARGE) + inject
    update = wanted.get(UrlField.SW_UPDATE)
    return [
        f'sys configuration {UrlField.MARGE} "{marge}"',
        f'sys configuration {UrlField.BMX_REGISTRY} "{wanted.get(UrlField.BMX_REGISTRY)}"',
        f'sys configuration {UrlField.STATS} "{wanted.get(UrlField.STATS)}"',
        f'sys configuration {UrlField.SW_UPDATE} "{update}"',
        f'envswitch boseurls set "{marge}" "{update}"',
    ]


def build_enable_ssh_commands(service: str, *, full_config: bool = False) -> list[str]:
    """The telnet sequence that opens SSH on a speaker that has never had it.

    Two forms, and the order to try them in. The DEFAULT writes the injection through the
    persistence layer alone, which is the field-confirmed form and needs no reboot. `full_config`
    also puts it on the runtime `sys configuration` key and reboots, which is what devices need
    when the value demonstrably persists but sshd never comes up.

    Neither form does anything on an unpaired speaker. A factory-reset device with an empty
    margeAccountUUID does not read margeServerUrl at all, so the injection has no read cycle to
    fire on and fails silently. Check the account before running either.
    """
    wanted = service_urls(service)
    marge = wanted.get(UrlField.MARGE) + SSH_INJECT
    update = wanted.get(UrlField.SW_UPDATE)
    envswitch = f'envswitch boseurls set "{marge}" "{update}"'
    if not full_config:
        return [envswitch]
    return [
        f'sys configuration {UrlField.BMX_REGISTRY} "{wanted.get(UrlField.BMX_REGISTRY)}"',
        f'sys configuration {UrlField.STATS} "{wanted.get(UrlField.STATS)}"',
        f'sys configuration {UrlField.MARGE} "{marge}"',
        f'sys configuration {UrlField.SW_UPDATE} "{update}"',
        envswitch,
        "getpdo CurrentSystemConfiguration",
        "sys reboot",
    ]


def orion_location(stream_url: str, name: str, *, image_url: str = "", service: str = "") -> str:
    """Wrap a stream URL as a preset location the speaker can actually follow.

    A LOCAL_INTERNET_RADIO location is FOLLOWED by the speaker, which expects a station document
    describing the stream. Given the stream URL itself the speaker receives audio where it expected
    a document, holds the source about twenty seconds and discards it without ever buffering.

    Without `service` this is the RELATIVE form `/station?data=...`, which the speaker resolves
    against the LOCAL_INTERNET_RADIO baseUrl from its BMX registry, so the preset keeps playing when
    the service moves to another address. That makes the registry load-bearing: it must advertise
    an address the speaker can reach (see registry_verdict). With `service` the host is written in
    front, the absolute form, for firmware that cannot resolve a relative location.

    Mirrors `BuildOrionLocation` in upstream's pkg/service/bmx/bmx.go (checked against v0.138.0)
    byte for byte: the JSON carries Go's field order and its escaping of < > & U+2028 U+2029, the
    base64 is the standard alphabet with padding, and the query escaping matches url.QueryEscape.
    A copy can drift, so `check` reads the stream back out of the `data` blob rather than comparing
    strings: a format change upstream shows up there as an alarm, not as silent rewrites.
    """
    payload = json.dumps({"name": name, "imageUrl": image_url, "streamUrl": stream_url},
                         separators=(",", ":"), ensure_ascii=False)
    for char, escaped in _GO_JSON_ESCAPES.items():
        payload = payload.replace(char, escaped)
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    relative = f"{ORION_STATION_PATH}?data={urllib.parse.quote_plus(encoded, safe='')}"
    return f"{service.rstrip('/')}{ORION_BASE_PATH}{relative}" if service else relative


def relative_orion_location(location: str) -> str:
    """The relative form of an Orion station location, or "" if it is not one.

    A port of upstream's models.RelativeOrionLocation (v0.138.0): the relative form comes back as it
    is, and an absolute one loses everything up to and including the Orion base path, WHATEVER the
    host - a bose.io one included, because the relative form resolves against this service's
    registry rather than against the host it names.
    """
    location = location.strip()
    if location.startswith(ORION_STATION_PATH):
        rest = location[len(ORION_STATION_PATH):]
        return location if rest == "" or rest.startswith("?") else ""
    parsed = urllib.parse.urlsplit(location)
    if not parsed.scheme or not parsed.netloc or parsed.path != ORION_PATH:
        return ""
    return ORION_STATION_PATH + (f"?{parsed.query}" if parsed.query else "")


def _relative_location(item: ET.Element) -> str:
    """The relative Orion form for one radio slot, or "" if it has none or already holds it.

    An absolute Orion location loses its host, the blob untouched. A legacy /custom/v1/playback
    location, which the player's older catalog entries still write, is rebuilt from the stream URL
    its base64 carries, named and pictured as the slot already is; one that does not decode is
    left alone, since rewriting it would store a station with no stream.
    """
    old = item.get("location", "")
    stream = decode_playback_location(old)
    if stream:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(old).query)
        name = (item.findtext("itemName") or "").strip() or query.get("name", [""])[0]
        image = (item.findtext("containerArt") or "").strip()
        return orion_location(stream, name, image_url=image)
    new = relative_orion_location(old)
    return "" if new == old else new


def relativize_plan(raw: str) -> list[RelativizeStep]:
    """Which buttons hold a HOST-BOUND radio location, and the storePreset body that fixes each.

    Host-bound means an absolute Orion location or a decodable legacy /custom/v1/playback one: both
    name a host, so the button stops playing when that host moves or is gone. The body carries the
    slot's ContentItem exactly as the speaker reported it - name, art, source, type, account - with
    only the location swapped for its relative form, so the station, its picture and its button are
    what they were. Only LOCAL_INTERNET_RADIO is touched: a TuneIn or Spotify location is not an
    Orion station however it is spelled.

    This reads the raw document with ElementTree rather than taking the parsed Presets, because the
    body it builds IS the speaker's own element, re-serialized with one attribute changed.
    """
    try:
        root = ET.fromstring(raw)  # noqa: S314 - the speaker's own /presets on the LAN
    except ET.ParseError:
        return []
    plan: list[RelativizeStep] = []
    for preset in root.iter("preset"):
        item = preset.find("ContentItem")
        if item is None or item.get("source") != RadioSource.LOCAL_INTERNET_RADIO:
            continue
        old = item.get("location", "")
        new = _relative_location(item)
        if not new:
            continue
        item.set("location", new)
        body = f'<preset id="{preset.get("id")}">{ET.tostring(item, encoding="unicode")}</preset>'
        plan.append(RelativizeStep(button=int(preset.get("id", "0")), old=old, new=new, body=body))
    return plan


def _origin(url: str) -> str:
    """scheme://host:port with the default port spelled out, so equal addresses compare equal."""
    parts = urllib.parse.urlsplit(url.strip())
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme, 0)
    return f"{parts.scheme}://{(parts.hostname or '').lower()}:{port}"


def _is_loopback(url: str) -> bool:
    host = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _advertised(body: str) -> dict[str, str]:
    """The TUNEIN and LOCAL_INTERNET_RADIO baseUrls a registry body names, or {} if unreadable."""
    judged = (RadioSource.TUNEIN, RadioSource.LOCAL_INTERNET_RADIO)
    try:
        services = json.loads(body).get("bmx_services")
        return {str(s["id"]["name"]): str(s["baseUrl"]) for s in services
                if s.get("id", {}).get("name") in judged}
    except (ValueError, AttributeError, TypeError, KeyError):
        return {}


def registry_verdict(service: str, body: str) -> RegistryCheck:
    """Does the BMX registry send speakers back to THIS service? ok, foreign, dns-mode, unreadable,
    or unjudged when `service` is a loopback address, which says nothing about what speakers use.

    Every radio source a speaker mounts comes from the baseUrl its registry names, and a relative
    preset resolves against it, so a registry naming another host breaks radio while every speaker
    still reads migrated. The usual cause is a copied service: AfterTouch's persisted
    settings.json `server_url` beats the SERVER_URL it was started with, so a container cloned from
    another install keeps advertising the machine it was cloned from. In AfterTouch's DNS mode the
    registry names content.api.bose.io on purpose, which is reported as such rather than as foreign.
    """
    advertised = _advertised(body)
    expected = _origin(service)

    def judged(verdict: RegistryVerdict, reason: str = "") -> RegistryCheck:
        return RegistryCheck(verdict=verdict, expected=expected, advertised=advertised, reason=reason)

    if _is_loopback(service):
        # The address this was run with names the service as THIS machine sees it, not as a
        # speaker does, so nothing it says about the registry's host can be judged against it.
        return judged(RegistryVerdict.UNJUDGED,
                      "the service address given is a loopback address, which no speakers "
                      "use; pass the address the speakers call back to")
    if set(advertised) != {RadioSource.TUNEIN, RadioSource.LOCAL_INTERNET_RADIO}:
        return judged(RegistryVerdict.UNREADABLE)
    origins = {_origin(url) for url in advertised.values()}
    if origins == {expected}:
        return judged(RegistryVerdict.OK)
    if all(is_cloud_location(url) for url in advertised.values()):
        return judged(RegistryVerdict.DNS_MODE)
    return judged(RegistryVerdict.FOREIGN)


def decode_playback_location(location: str) -> str:
    """Recover the stream URL a legacy /custom/v1/playback location wraps, or "" if it is not one."""
    if PLAYBACK_PATH not in location:
        return ""
    encoded = location.split(PLAYBACK_PATH, 1)[1].split("?", 1)[0]
    try:
        return base64.urlsafe_b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


_TO_STANDARD_BASE64 = str.maketrans("-_ ", "+/+")


def decode_cloud_location(location: str) -> str:
    """Recover the stream URL buried in a Bose adapter location, or "" if it is not one.

    A preset written while the Bose cloud was alive points at the Orion station adapter and carries
    the real stream inside its `data` query parameter, as base64url JSON with a `streamUrl` key. So
    an owner's own pre-shutdown presets usually already CONTAIN the direct stream, and harvesting
    beats hunting for it. Presets from a catalogue source such as TUNEIN carry a station id instead
    and yield nothing here, which is the case that has to be researched by hand.
    """
    query = urllib.parse.urlparse(location).query
    blob = urllib.parse.parse_qs(query).get("data", [""])[0]
    if not blob:
        return ""
    try:
        # The cloud wrote base64url, and the standard decoder silently DROPS '-' and '_'. Mapping
        # them onto '+' and '/' reads either alphabet, since neither uses the other's two symbols.
        # A '+' left unescaped in the query reaches here as a space (parse_qs), so it maps back.
        padded = blob.translate(_TO_STANDARD_BASE64) + "=" * (-len(blob) % 4)
        return str(json.loads(base64.b64decode(padded, validate=True)).get("streamUrl", ""))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return ""


def stream_url_from_location(location: str) -> str:
    """The plain stream URL a stored location stands for, whichever form it is in, or "".

    Three forms reach this: our own playback wrapping, a surviving Bose cloud adapter URL, and a
    bare stream URL somebody wrote by hand. Anything else - a catalogue station id - has no stream
    in it to find.
    """
    for decoded in (decode_playback_location(location), decode_cloud_location(location)):
        if decoded:
            return decoded
    if location.startswith(("http://", "https://")) and not is_cloud_location(location):
        return location
    return ""


def is_cloud_location(location: str) -> bool:
    """Does this location still point into Bose's dead infrastructure?"""
    host = urllib.parse.urlparse(location).netloc.lower()
    return any(marker in host for marker in CLOUD_MARKERS)


def harvest_presets(presets: Presets) -> list[PresetEntry]:
    """Turn a speaker's stored presets into template entries, saying which ones need research.

    An entry whose `location` is empty could not be resolved to a stream and is NOT a usable
    preset: it is a name to look up. Emitting it rather than dropping it is deliberate, so the
    button and the station name survive into whatever replaces it.

    Only radio is turned into a stream entry. A Spotify album or a media-server track is carried
    over exactly as stored and marked `keep`: this skill does not manage it, and rewriting it as
    LOCAL_INTERNET_RADIO would turn the owner's album into a radio station without saying so.
    """
    out: list[PresetEntry] = []
    for button, item in sorted(presets.items.items()):
        name = item.name or f"preset {button}"
        if item.source and item.source not in RADIO_SOURCES:
            out.append(PresetEntry(button_number=button, name=name, location=item.location,
                                   content_item_type=item.type, source=item.source, keep=True))
            continue
        out.append(PresetEntry(button_number=button, name=name,
                               location=stream_url_from_location(item.location)))
    return out


def _preset_name(raw: str, button: int) -> str:
    """The itemName the speaker shows for one button, or "" when it has none."""
    for chunk in raw.split("<preset ")[1:]:
        if f'id="{button}"' not in chunk.split(">", 1)[0]:
            continue
        if "<itemName>" in chunk:
            # Unescaped here because preset_xml escapes on the way back out; kept escaped, a
            # "Rock &amp; Roll" becomes "Rock &amp;amp; Roll" on the button after one restore.
            return html.unescape(chunk.split("<itemName>", 1)[1].split("</itemName>", 1)[0].strip())
    return ""


def classify_stream(status: int | None, content_type: str, head: str) -> StreamKind:
    """What a fetch of a candidate stream URL actually returned.

    The content type alone is not enough, and trusting it is the trap: an .m3u playlist is served
    as `audio/x-mpegurl`, so a check for `audio/` passes a text file that contains no audio at all,
    and the preset is then accepted at write time and never plays. HLS is separated out because
    resolving it gains nothing - the speaker cannot play a segment list.
    """
    if status is None or status >= 400:
        return StreamKind.DEAD
    ctype = content_type.split(";", 1)[0].strip().lower()
    if "#EXT-X-" in head:
        return StreamKind.HLS
    if ctype in PLAYLIST_TYPES or head.lstrip().startswith(("#EXTM3U", "[playlist]")):
        return StreamKind.PLAYLIST
    if ctype.startswith("audio/") or ctype in ("application/ogg", "video/mp2t"):
        return StreamKind.AUDIO
    return StreamKind.NOT_AUDIO


def playlist_targets(body: str) -> list[str]:
    """The stream URLs listed inside an .m3u or .pls body, in order."""
    found: list[str] = []
    for line in body.splitlines():
        text = line.strip()
        if text.startswith("#") or not text:
            continue
        candidate = text.split("=", 1)[1].strip() if text.lower().startswith("file") else text
        if candidate.startswith(("http://", "https://")) and candidate not in found:
            found.append(candidate)
    return found


def _attr(tag: str, name: str) -> str:
    """One attribute's value from an opening tag, unescaped, or "" when the tag does not carry it.

    Unescaped because the speaker answers in XML: a URL holding `&` arrives as `&amp;`, and kept
    that way it never equals the plain URL it stands for.
    """
    marker = f' {name}="'
    return html.unescape(tag.split(marker, 1)[1].split('"', 1)[0]) if marker in tag else ""


def parse_presets(raw: str) -> Presets:
    """Read a speaker's /presets once: which BUTTON holds what, and every stored location.

    The speaker returns `<preset id="N">` wrapping each ContentItem, so the button number is on the
    outer tag. Reading only the locations loses it, and then a station sitting on the wrong button
    cannot be told apart from one that is correct. The source is what separates a radio station
    from an album, so dropping it flattens every preset into radio.
    """
    items: dict[int, PresetItem] = {}
    for chunk in raw.split("<preset ")[1:]:
        if 'id="' not in chunk or 'location="' not in chunk:
            continue
        try:
            button = int(chunk.split('id="', 1)[1].split('"', 1)[0])
        except ValueError:
            continue
        tag = " " + chunk.split("<ContentItem", 1)[-1].split(">", 1)[0]
        items[button] = PresetItem(button=button, location=_attr(tag, "location"),
                                   source=_attr(tag, "source"), type=_attr(tag, "type"),
                                   name=_preset_name(raw, button))
    locations = tuple(chunk.split('location="', 1)[1].split('"', 1)[0]
                      for chunk in raw.split("<ContentItem")[1:] if 'location="' in chunk)
    return Presets(items=items, locations=locations)


def slots_to_write(presets: Presets, wanted: Sequence[PresetEntry]) -> list[PresetEntry]:
    """The template entries whose BUTTON does not already hold their stream.

    Two readings this rules out. Counting says six presets are present when one of them now points
    at a station the owner replaced. And comparing streams alone says nothing is missing when the
    right station sits on the wrong button, so a corrected template would never be applied - the
    button is part of what the owner asked for, not incidental.

    A slot is compared by the STREAM it stands for, whatever form stores it: the Orion form the
    player and this skill write, the older /custom/v1/playback form, or a bare URL. Comparing the
    location string, or decoding only one form, calls a correct slot missing forever, and a restore
    then rewrites the owner's button on every run. Entries marked `keep` are never written.
    """
    slots = presets.slots()
    return [p for p in wanted if not p.keep
            and stream_url_from_location(slots.get(p.button_number, "")) != p.location]


def missing_streams(presets: Presets, wanted: Sequence[PresetEntry]) -> list[str]:
    """Just the stream URLs from slots_to_write, for reporting."""
    return [p.location for p in slots_to_write(presets, wanted)]


def port_open(ip: str, port: int, timeout: float = 3.0) -> bool:
    """Is the port accepting connections?

    A real socket rather than the `echo > /dev/tcp/host/port` shell redirect, which is a bash
    builtin: run under sh (dash on Debian and Ubuntu, and what docker exec or ssh host 'cmd' often
    give you) it fails for every port, so a live service reports as closed.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


class PromptSocket(Protocol):
    """The two socket calls `read_to_prompt` makes, so a scripted fake can stand in for a speaker."""

    def settimeout(self, value: float | None, /) -> None: ...

    def recv(self, bufsize: int, /) -> bytes: ...


def read_to_prompt(sock: PromptSocket, timeout: float = 10.0) -> tuple[str, bool]:
    """Read until the `->` prompt. Returns the text and whether the prompt actually arrived.

    Waiting for OK would hang: `envswitch boseurls set` replies `Setting Bose Server URLs to ...`
    and never says OK, while every command does end at the prompt.

    The flag matters because a timeout returns whatever arrived so far. Reported as an ordinary
    reply, a truncated read is indistinguishable from a complete one, so a command that never
    finished reads as one that succeeded.
    """
    sock.settimeout(timeout)
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        if buf.rstrip().endswith(PROMPT):
            return buf.decode("utf-8", "replace"), True
    return buf.decode("utf-8", "replace"), False


def telnet_run(ip: str, commands: list[str], settle: float = 0.2) -> list[TelnetReply]:
    """Send commands to the diagnostic port in order and collect each reply."""
    out: list[TelnetReply] = []
    try:
        with socket.create_connection((ip, TELNET_PORT), timeout=10) as sock:
            read_to_prompt(sock, timeout=6)
            for cmd in commands:
                sock.sendall(cmd.encode() + b"\r\n")
                reply, complete = read_to_prompt(sock)
                out.append(TelnetReply(cmd=cmd, reply=reply.strip(), complete=complete))
                time.sleep(settle)
    except OSError as exc:
        raise SpeakerError(f"diagnostic port {TELNET_PORT} on {ip}: {exc}") from exc
    return out


def http_get(url: str, timeout: float = 8.0) -> str:
    if not url.startswith(("http://", "https://")):
        raise SpeakerError(f"refusing non-http URL: {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
            return resp.read().decode("utf-8", "replace")
    except OSError as exc:
        raise SpeakerError(f"{url}: {exc}") from exc


def json_object(value: object) -> dict[str, object] | None:
    """A decoded JSON object as a typed mapping, or None for anything else.

    `isinstance(value, dict)` alone narrows to a dict of unknown types, which hides every later
    misuse of its values; JSON object keys are always strings, so this is the honest type.
    """
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def json_list(value: object) -> list[object] | None:
    """A decoded JSON array as a typed list, or None for anything else."""
    return cast("list[object]", value) if isinstance(value, list) else None


def http_date_header(ip: str, timeout: float = 8.0) -> str | None:
    """The Date header the speaker's own web server sends, or None if it did not answer.

    This is how the clock of a speaker with NO SSH can still be read: its HTTP server renders its
    system clock into every response. Nothing on that port can SET a clock, so this is a diagnosis
    only. A failure is a None rather than an exception, because a speaker that does not answer is a
    normal reading here and must not stop the rest of the survey.
    """
    try:
        with urllib.request.urlopen(f"http://{ip}:{API_PORT}/info",  # noqa: S310 - literal scheme
                                    timeout=timeout) as resp:
            return resp.headers.get("Date")
    except (OSError, ValueError):
        return None


def clock_state(header: str | None, now: float | None = None,
                *, tolerance_s: int = CLOCK_TOLERANCE_S) -> ClockState:
    """Judge a speaker's clock from its Date header: ok, wrong, or unknown.

    `reading` is the wall clock the box itself reported, printed as the box printed it, because that
    is what an owner needs to see - "2015-07-06 20:36:50" says what no verdict word can. The
    comparison is deliberately coarse; see CLOCK_TOLERANCE_S for why a day is both necessary and
    sufficient.
    """
    if not header:
        return ClockState(ClockVerdict.UNKNOWN, None)
    try:
        stamp = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return ClockState(ClockVerdict.UNKNOWN, None)
    epoch = stamp.timestamp()
    moment = time.time() if now is None else now
    return ClockState(ClockVerdict.OK if abs(epoch - moment) <= tolerance_s else ClockVerdict.WRONG,
                      time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch)))
