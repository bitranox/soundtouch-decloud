#!/usr/bin/env python3
"""Find the speakers and say what state each one is in.

    uv run scripts/soundtouch_find.py --service http://192.0.2.10:8000
    uv run scripts/soundtouch_find.py --ip 192.0.2.31

Prints a JSON envelope with one verdict per speaker, the first that applies of: not-answering,
needs-migration, registry-foreign, unreadable, needs-account, sources-not-ready, needs-presets,
clock-wrong, ready. Exit 0 when every speaker answered, whatever its verdict; 1 when one did not
or none was found; 2 when the service could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

try:
    from soundtouch_core import (API_PORT, SSH_PORT, TELNET_PORT, ClockState, ClockVerdict,
                                 RadioSources, RegistryCheck, RegistryVerdict, ServiceUrls,
                                 SpeakerError, UrlField, clock_state, http_date_header, http_get,
                                 parse_presets, parse_sources, parse_urls, port_open,
                                 json_list, json_object, registry_verdict, telnet_run)
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from soundtouch_core import (API_PORT, SSH_PORT, TELNET_PORT, ClockState, ClockVerdict,
                                 RadioSources, RegistryCheck, RegistryVerdict, ServiceUrls,
                                 SpeakerError, UrlField, clock_state, http_date_header, http_get,
                                 parse_presets, parse_sources, parse_urls, port_open,
                                 json_list, json_object, registry_verdict, telnet_run)

__all__ = ["DeviceInfo", "DiscoveredDevice", "SpeakerState", "SpeakerVerdict", "build_parser",
           "classify", "describe_state", "parse_info", "speaker_state", "main"]

# Probed in this order; the order is also the key order of the printed "ports" object.
PROBED_PORTS = (SSH_PORT, TELNET_PORT, API_PORT)


class SpeakerVerdict(StrEnum):
    """One word for what to do about a speaker next."""

    NOT_ANSWERING = "not-answering"
    NEEDS_MIGRATION = "needs-migration"
    REGISTRY_FOREIGN = "registry-foreign"
    UNREADABLE = "unreadable"
    NEEDS_ACCOUNT = "needs-account"
    SOURCES_NOT_READY = "sources-not-ready"
    NEEDS_PRESETS = "needs-presets"
    CLOCK_WRONG = "clock-wrong"
    READY = "ready"


@dataclass(frozen=True)
class DeviceInfo:
    """The identity fields of a speaker's /info. name and device_id are None when the tag is
    missing; account is "" when no account is attached."""

    name: str | None
    device_id: str | None
    account: str


@dataclass(frozen=True)
class DiscoveredDevice:
    """One speaker as the AfterTouch service lists it."""

    name: str | None
    device_id: str | None
    ip_address: str | None

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "device_id": self.device_id, "ip": self.ip_address}


@dataclass(frozen=True)
class SpeakerState:
    """Everything read from one speaker. Each optional part is None when it was not read, and the
    matching *_error says why when it failed; the verdict and advice are derived, never stored."""

    ip: str
    ports: Mapping[int, bool]
    info: DeviceInfo | None = None
    info_error: str | None = None
    urls: ServiceUrls | None = None
    cloud_leftovers: ServiceUrls | None = None
    telnet_error: str | None = None
    registry: RegistryCheck | None = None
    sources: RadioSources | None = None
    sources_error: str | None = None
    preset_count: int | None = None
    presets_error: str | None = None
    clock: ClockState | None = None

    @property
    def verdict(self) -> SpeakerVerdict:
        return classify(self)

    @property
    def advice(self) -> str:
        return describe_state(self.verdict)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"ip": self.ip,
                                  "ports": {str(port): up for port, up in self.ports.items()}}
        if not self.ports.get(API_PORT):
            return out | self._verdict_json()
        out |= self._info_json() | self._telnet_json()
        if self.registry is not None:
            out["registry"] = self.registry.to_json()
        out |= self._sources_json() | self._presets_json()
        if self.clock is not None:
            out["clock"] = self.clock.to_json()
        return out | self._verdict_json()

    def _verdict_json(self) -> dict[str, object]:
        return {"verdict": self.verdict.value, "advice": self.advice}

    def _info_json(self) -> dict[str, object]:
        if self.info_error is not None:
            return {"info_error": self.info_error}
        if self.info is None:
            return {}
        return {"name": self.info.name, "device_id": self.info.device_id,
                "account": self.info.account}

    def _telnet_json(self) -> dict[str, object]:
        if self.urls is not None and self.cloud_leftovers is not None:
            return {"urls": self.urls.to_json(), "cloud_leftovers": self.cloud_leftovers.to_json()}
        if self.telnet_error is not None:
            return {"telnet_error": self.telnet_error}
        return {}

    def _sources_json(self) -> dict[str, object]:
        if self.sources is not None:
            return {"sources": self.sources.to_json()}
        if self.sources_error is not None:
            return {"sources_error": self.sources_error}
        return {}

    def _presets_json(self) -> dict[str, object]:
        if self.preset_count is not None:
            return {"preset_count": self.preset_count}
        if self.presets_error is not None:
            return {"presets_error": self.presets_error}
        return {}


def classify(state: SpeakerState) -> SpeakerVerdict:
    """One word for what to do about this speaker next."""
    if not state.ports.get(API_PORT):
        return SpeakerVerdict.NOT_ANSWERING
    if state.cloud_leftovers:
        return SpeakerVerdict.NEEDS_MIGRATION
    # Right after migration, because it is the fault that hides behind a finished one: every URL
    # names the service, every source reads READY, and radio still fails. Only "foreign" counts;
    # an unreadable registry is not knowing, and DNS mode names the Bose cloud on purpose.
    if state.registry is not None and state.registry.verdict == RegistryVerdict.FOREIGN:
        return SpeakerVerdict.REGISTRY_FOREIGN
    # A failed read never saw what it was asked for: no account, no sources and no presets must
    # not be claimed from a question the speaker did not answer. The *_error fields say which.
    if any(error is not None
           for error in (state.info_error, state.sources_error, state.presets_error)):
        return SpeakerVerdict.UNREADABLE
    if state.info is None or not state.info.account:
        return SpeakerVerdict.NEEDS_ACCOUNT
    if state.sources is not None and not state.sources.radio_ready():
        return SpeakerVerdict.SOURCES_NOT_READY
    if not state.preset_count:
        return SpeakerVerdict.NEEDS_PRESETS
    # Last, because every fault above is both more actionable and able to produce a misleading
    # clock reading on a box that is still coming up. A clock that could not be read at all leaves
    # the verdict alone: not knowing is not a fault.
    if state.clock is not None and state.clock.verdict == ClockVerdict.WRONG:
        return SpeakerVerdict.CLOCK_WRONG
    return SpeakerVerdict.READY


_ADVICE: Mapping[SpeakerVerdict, str] = {
    SpeakerVerdict.NOT_ANSWERING: (
        "This speaker did not answer. Press a button on it to wake it, then try "
        "again. If it still does not answer, check it is on the same network as "
        "the service and not on a guest network."),
    SpeakerVerdict.NEEDS_MIGRATION: (
        "This speaker is still trying to reach the Bose cloud, which no longer "
        "exists. It needs its service addresses rewritten."),
    SpeakerVerdict.REGISTRY_FOREIGN: (
        "This speaker is set up correctly, but the service it asks for its "
        "radio sources sends it to a different address, so stations and "
        "presets fail. The service's own settings name another machine - "
        "usually because it was copied from another install. Fix server_url "
        "in the service's settings.json (or its Settings page), restart it, "
        "then restart every speaker: a speaker reads these addresses when it "
        "starts and keeps the old ones until it restarts."),
    SpeakerVerdict.UNREADABLE: (
        "This speaker answers on the network but did not answer every question "
        "about itself (its account, radio sources or presets), so nothing can be "
        "said about those yet. If it was just restarted, give it about 90 "
        "seconds and look again; if it stays this way, restart it."),
    SpeakerVerdict.NEEDS_ACCOUNT: (
        "This speaker has no account attached, so it will not load any radio at "
        "all until one is bound to it."),
    SpeakerVerdict.SOURCES_NOT_READY: (
        "This speaker has not finished loading its radio sources. If it was "
        "just restarted, give it about 90 seconds and look again."),
    SpeakerVerdict.NEEDS_PRESETS: "This speaker is working but has no presets on it yet.",
    SpeakerVerdict.CLOCK_WRONG: (
        "This speaker's clock is years out, which happens after a power cut because "
        "it has no battery to keep time. Everything else about it is fine, but no "
        "https station will play until the clock is put right - a plain http one "
        "still will, which is how to confirm it. If its SSH is open, one ntpd "
        "command fixes it; if not, it can only be done at the speaker."),
    SpeakerVerdict.READY: "This speaker is set up and working.",
}


def describe_state(verdict: SpeakerVerdict) -> str:
    """What to tell a non-technical owner, in their words rather than ours."""
    return _ADVICE[verdict]


def parse_info(raw: str) -> DeviceInfo:
    """Read name, deviceID and account out of a speaker's /info body."""
    name = raw.split("<name>", 1)[1].split("</name>", 1)[0] if "<name>" in raw else None
    device_id = raw.split('deviceID="', 1)[1].split('"', 1)[0] if 'deviceID="' in raw else None
    account = (raw.split("<margeAccountUUID>", 1)[1].split("</", 1)[0]
               if "<margeAccountUUID>" in raw else "")
    return DeviceInfo(name=name, device_id=device_id, account=account)


def _read_info(ip: str) -> tuple[DeviceInfo | None, str | None]:
    try:
        return parse_info(http_get(f"http://{ip}:{API_PORT}/info")), None
    except SpeakerError as exc:
        return None, str(exc)


def _read_urls(ip: str) -> tuple[ServiceUrls | None, str | None]:
    try:
        return parse_urls(telnet_run(ip, ["getpdo CurrentSystemConfiguration"])[0].reply), None
    except SpeakerError as exc:
        return None, str(exc)


def _read_registry(urls: ServiceUrls | None, leftovers: ServiceUrls | None) -> RegistryCheck | None:
    registry_url = urls.get(UrlField.BMX_REGISTRY) if urls is not None else ""
    if not registry_url or leftovers:
        return None
    # The registry THIS speaker reads, not the one the operator thinks it reads: the two differ
    # exactly when something is wrong.
    try:
        return registry_verdict(registry_url, http_get(registry_url))
    except SpeakerError as exc:
        return RegistryCheck(verdict=RegistryVerdict.UNREADABLE, error=str(exc))


def _read_sources(ip: str) -> tuple[RadioSources | None, str | None]:
    try:
        return parse_sources(http_get(f"http://{ip}:{API_PORT}/sources")), None
    except SpeakerError as exc:
        return None, str(exc)


def _read_preset_count(ip: str) -> tuple[int | None, str | None]:
    try:
        return len(parse_presets(http_get(f"http://{ip}:{API_PORT}/presets")).locations), None
    except SpeakerError as exc:
        return None, str(exc)


def speaker_state(ip: str) -> SpeakerState:
    """Everything worth knowing about one speaker, without changing anything."""
    ports = {port: port_open(ip, port) for port in PROBED_PORTS}
    if not ports[API_PORT]:
        return SpeakerState(ip=ip, ports=ports)
    info, info_error = _read_info(ip)
    urls, telnet_error = _read_urls(ip) if ports[TELNET_PORT] else (None, None)
    leftovers = urls.cloud_leftovers() if urls is not None else None
    sources, sources_error = _read_sources(ip)
    preset_count, presets_error = _read_preset_count(ip)
    return SpeakerState(
        ip=ip, ports=ports, info=info, info_error=info_error, urls=urls, cloud_leftovers=leftovers,
        telnet_error=telnet_error, registry=_read_registry(urls, leftovers),
        sources=sources, sources_error=sources_error, preset_count=preset_count,
        presets_error=presets_error,
        # Read from the speaker's own Date header, so this works on a box whose SSH is closed - the
        # one place where a wrong clock is otherwise invisible and where nothing can repair it
        # remotely.
        clock=clock_state(http_date_header(ip)))


def _discover(service: str) -> list[DiscoveredDevice]:
    body = http_get(f"{service.rstrip('/')}/api/setup/devices")
    try:
        found = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SpeakerError("the service answered but not with JSON") from exc
    entries = json_list(found)
    if entries is None:
        raise SpeakerError("the service's device list is not a JSON array")
    objects = (o for o in map(json_object, entries) if o is not None)
    return [DiscoveredDevice(name=_text(d.get("name")), device_id=_text(d.get("device_id")),
                             ip_address=_text(d.get("ip_address")))
            for d in objects]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", help="AfterTouch base URL, to discover speakers")
    parser.add_argument("--ip", action="append", default=[], help="check this speaker directly")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.service and not args.ip:
        parser.error("give --service to discover, or --ip to check one speaker")

    data: dict[str, object] = {}
    targets = list(args.ip)
    if args.service:
        try:
            devices = _discover(args.service)
        except SpeakerError as exc:
            print(json.dumps({"ok": False, "command": "find", "data": {"error": str(exc)}}, indent=2))
            return 2
        data["discovered"] = [d.to_json() for d in devices]
        targets += [d.ip_address for d in devices if d.ip_address]

    seen: list[str] = []
    states: list[SpeakerState] = []
    for ip in targets:
        if ip in seen:
            continue
        seen.append(ip)
        states.append(speaker_state(ip))
    data["speakers"] = [state.to_json() for state in states]
    ok = bool(states) and all(s.verdict != SpeakerVerdict.NOT_ANSWERING for s in states)
    if not ok:
        data["next"] = ("At least one speaker did not answer. Ask the owner to press a button on "
                        "it, then run this again.")
    print(json.dumps({"ok": ok, "command": "find", "data": data}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
