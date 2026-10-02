"""Tests for the parsing and command-building rules.

Every case here is a real reading that a plausible implementation gets wrong, so each test is
written against the payload shape the speaker actually returns rather than a tidied-up sample.
"""
import base64
import json

import pytest
import soundtouch_core as C
from soundtouch_core import (
    ClockVerdict,
    PresetEntry,
    RadioSource,
    RegistryVerdict,
    ServiceUrls,
    UrlField,
)

# getpdo puts the value on the line AFTER the field name.
GETPDO = """->getpdo CurrentSystemConfiguration
CurrentSystemConfiguration {
  margeServerUrl {
    text: "http://192.0.2.10:8000"
  }
  statsServerUrl {
    text: "https://events.api.bosecm.com"
  }
  swUpdateUrl {
    text: "http://192.0.2.10:8000/updates/soundtouch"
  }
  bmxRegistryUrl {
    text: "https://content.api.bose.io/bmx/registry/v1/services"
  }
}
->"""

# /sources entries are self-closing and carry no text label.
SOURCES = (
    '<?xml version="1.0" encoding="UTF-8" ?><sources deviceID="00005E005300">'
    '<sourceItem source="TUNEIN" status="READY" isLocal="false" multiroomallowed="true" />'
    '<sourceItem source="LOCAL_INTERNET_RADIO" status="READY" isLocal="false" />'
    '<sourceItem source="BLUETOOTH" status="UNAVAILABLE" isLocal="true" />'
    "</sources>"
)


def test_parse_urls_reads_the_value_from_the_following_line():
    urls = C.parse_urls(GETPDO)
    assert urls.get(UrlField.MARGE) == "http://192.0.2.10:8000"
    assert urls.get(UrlField.BMX_REGISTRY) == "https://content.api.bose.io/bmx/registry/v1/services"
    assert len(urls) == 4


def test_parse_urls_ignores_a_field_with_no_value():
    assert "margeServerUrl" not in C.parse_urls("margeServerUrl {\n}\n->")


def test_parse_urls_of_empty_input_is_empty():
    assert not C.parse_urls("")


def test_cloud_leftovers_flags_every_bose_domain():
    """Clearing only bose.com leaves bmxRegistryUrl on bose.io, and radio never mounts."""
    left = C.parse_urls(GETPDO).cloud_leftovers()
    assert set(left.to_json()) == {"statsServerUrl", "bmxRegistryUrl"}


def test_cloud_leftovers_empty_when_fully_local():
    assert C.service_urls("http://192.0.2.10:8000").cloud_leftovers().to_json() == {}


def test_injected_values_spots_leftover_shell_text():
    urls = ServiceUrls({UrlField.MARGE: "http://192.0.2.10:8000;touch /tmp/x",
                        UrlField.SW_UPDATE: "http://192.0.2.10:8000"})
    assert list(urls.injected().to_json()) == ["margeServerUrl"]


def test_parse_sources_matches_the_attribute_not_a_label():
    got = C.parse_sources(SOURCES)
    assert got.statuses[RadioSource.TUNEIN] == "READY"
    assert got.statuses[RadioSource.LOCAL_INTERNET_RADIO] == "READY"


def test_parse_sources_reports_a_missing_source_as_absent():
    """A source the speaker never published must not read as READY."""
    assert C.parse_sources(SOURCES).statuses[RadioSource.RADIO_BROWSER] == "ABSENT"


def test_parse_sources_keeps_a_non_ready_status():
    payload = SOURCES.replace('source="TUNEIN" status="READY"', 'source="TUNEIN" status="UNAVAILABLE"')
    assert C.parse_sources(payload).statuses[RadioSource.TUNEIN] == "UNAVAILABLE"


def test_envswitch_comes_last():
    """envswitch SAVES the runtime state, so writing it first discards everything after it."""
    cmds = C.build_url_commands("http://192.0.2.10:8000")
    assert cmds[-1].startswith("envswitch boseurls set")
    assert sum(c.startswith("envswitch") for c in cmds) == 1


def test_all_four_fields_go_through_sys_configuration():
    """envswitch carries only two URLs, so bmxRegistry exists only if sys configuration writes it."""
    cmds = C.build_url_commands("http://192.0.2.10:8000")
    written = {c.split()[2] for c in cmds if c.startswith("sys configuration")}
    assert written == {"margeServerUrl", "statsServerUrl", "swUpdateUrl", "bmxRegistryUrl"}


def test_injection_lands_on_marge_only_and_in_both_places():
    inject = ";touch /tmp/remote_services;/etc/init.d/sshd start"
    cmds = C.build_url_commands("http://192.0.2.10:8000", inject=inject)
    marge = [c for c in cmds if c.startswith("sys configuration margeServerUrl")][0]
    bmx = [c for c in cmds if c.startswith("sys configuration bmxRegistryUrl")][0]
    assert inject in marge and inject not in bmx
    assert inject in cmds[-1]


def test_no_injection_by_default():
    assert all(";" not in c for c in C.build_url_commands("http://192.0.2.10:8000"))


def test_service_urls_tolerate_a_trailing_slash():
    assert C.service_urls("http://192.0.2.10:8000/").get(UrlField.SW_UPDATE).count("//") == 1


SERVICE = "http://192.0.2.10:8000"

# Produced by upstream's own code run in Go, not by this code: bmx.BuildOrionLocation at v0.138.0
# (pkg/service/bmx/bmx.go) for the relative form, and SERVICE + models.OrionBasePath + that for the
# absolute one, which is byte-identical to what BuildOrionLocation wrote at v0.137.1. Byte equality
# is the point: the player compares, catalogues and shares what it stores, so a location that
# decodes the same but is spelled differently is a second format.
ORION_ABSOLUTE_GOLDEN = [
    (("Example Radio", "", "https://radio.example.com/live.mp3"),
     "http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data="
     "eyJuYW1lIjoiRXhhbXBsZSBSYWRpbyIsImltYWdlVXJsIjoiIiwic3RyZWFtVXJsIjoiaHR0cHM6Ly9yYWRpby5leGFtc"
     "GxlLmNvbS9saXZlLm1wMyJ9"),
    # Go's json.Marshal escapes < > & as < > & and keeps non-ASCII as UTF-8.
    (("Ö1 <live> & more", "https://img.example.com/a.png?x=1&y=2",
      "https://stream.example.at/oe1-q2a.mp3?ua=SoundTouch&n=1"),
     "http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data="
     "eyJuYW1lIjoiw5YxIFx1MDAzY2xpdmVcdTAwM2UgXHUwMDI2IG1vcmUiLCJpbWFnZVVybCI6Imh0dHBzOi8vaW1nLmV4Y"
     "W1wbGUuY29tL2EucG5nP3g9MVx1MDAyNnk9MiIsInN0cmVhbVVybCI6Imh0dHBzOi8vc3RyZWFtLmV4YW1wbGUuYXQvb2"
     "UxLXEyYS5tcDM%2FdWE9U291bmRUb3VjaFx1MDAyNm49MSJ9"),
    # ... and U+2028 too; the padding '=' is query-escaped.
    (("line sep", "", "http://s.example.com/a b"),
     "http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data="
     "eyJuYW1lIjoibGluZVx1MjAyOHNlcCIsImltYWdlVXJsIjoiIiwic3RyZWFtVXJsIjoiaHR0cDovL3MuZXhhbXBsZS5jb"
     "20vYSBiIn0%3D"),
]


# The relative form: the absolute one with SERVICE and the Orion base path cut off.
ORION_RELATIVE_GOLDEN = [(args, expected[len(SERVICE + C.ORION_BASE_PATH):])
                         for args, expected in ORION_ABSOLUTE_GOLDEN]


def test_the_relative_golden_values_are_upstreams():
    """Pins the derivation above to a literal BuildOrionLocation v0.138.0 printed."""
    assert ORION_RELATIVE_GOLDEN[0][1] == (
        "/station?data=eyJuYW1lIjoiRXhhbXBsZSBSYWRpbyIsImltYWdlVXJsIjoiIiwic3RyZWFtVXJsIjoiaHR0c"
        "HM6Ly9yYWRpby5leGFtcGxlLmNvbS9saXZlLm1wMyJ9")


@pytest.mark.parametrize(("args", "expected"), ORION_RELATIVE_GOLDEN)
def test_orion_location_writes_the_relative_form_byte_identical_to_upstream(args, expected):
    name, image, stream = args
    assert C.orion_location(stream, name, image_url=image) == expected


@pytest.mark.parametrize(("args", "expected"), ORION_ABSOLUTE_GOLDEN)
def test_orion_location_writes_the_absolute_form_only_when_given_a_service(args, expected):
    name, image, stream = args
    assert C.orion_location(stream, name, image_url=image, service=SERVICE) == expected


@pytest.mark.parametrize(("args", "_expected"), ORION_ABSOLUTE_GOLDEN)
@pytest.mark.parametrize("service", ["", SERVICE])
def test_orion_location_round_trips(args, _expected, service):
    name, image, stream = args
    location = C.orion_location(stream, name, image_url=image, service=service)
    assert C.stream_url_from_location(location) == stream


def test_orion_location_tolerates_a_trailing_slash_on_the_service():
    assert C.orion_location("https://a.example.com/s", "A", service=SERVICE + "/") == \
        C.orion_location("https://a.example.com/s", "A", service=SERVICE)


# Cases upstream's models.RelativeOrionLocation answered at v0.138.0, run in Go.
@pytest.mark.parametrize(("location", "expected"), [
    ("/station?data=x", "/station?data=x"),
    ("https://h.example/core02/svc-bmx-adapter-orion/prod/orion/station?data=x", "/station?data=x"),
    ("http://h/custom/v1/playback/abc", ""),
    ("https://h.example/core02/svc-bmx-adapter-orion/prod/orion/stationX?data=x", ""),
    ("station?data=x", ""),
    ("/station", "/station"),
    ("", ""),
    ("https://radio.example.com/live.mp3", ""),
])
def test_relative_orion_location_matches_upstream(location, expected):
    assert C.relative_orion_location(location) == expected


@pytest.mark.parametrize(("args", "expected"), ORION_ABSOLUTE_GOLDEN)
def test_any_host_absolute_form_relativizes_to_the_upstream_relative_form(args, expected):
    rel = dict(ORION_RELATIVE_GOLDEN)[args]
    assert C.relative_orion_location(expected) == rel
    assert C.relative_orion_location(expected.replace(SERVICE, "https://content.api.bose.io")) == rel


def _legacy(stream: str) -> str:
    """The /custom/v1/playback form this skill wrote before 1.8.0, still on speakers today."""
    encoded = base64.urlsafe_b64encode(stream.encode()).decode()
    return f"{SERVICE}{C.PLAYBACK_PATH}{encoded}?name=S"


def test_the_legacy_playback_form_still_decodes():
    stream = "https://radio.example.com/stream?x=1&y=2"
    assert C.decode_playback_location(_legacy(stream)) == stream
    assert C.stream_url_from_location(_legacy(stream)) == stream


def test_decode_ignores_a_raw_stream_url():
    assert C.decode_playback_location("https://radio.example.com/stream") == ""


def _speaker_presets(*slots: tuple[int, str], form=None) -> str:
    """The shape /presets really returns: each ContentItem inside a <preset id="N"> wrapper.

    By default every slot holds what this skill now writes. `form` swaps in another builder, for
    a slot the player wrote or one left behind by an older version of this skill.
    """
    build = form or (lambda stream: C.orion_location(stream, "S"))
    return "<presets>" + "".join(
        f'<preset id="{button}"><ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
        f'location="{build(stream)}" /></preset>'
        for button, stream in slots) + "</presets>"


def test_a_slot_the_player_wrote_counts_as_correct():
    """AfterTouch's player and CLI store the Orion form, which must never read as missing.

    Reading only our own old wrapping made `check` exit 1 forever for these and made `restore`
    rewrite the owner's buttons on every run.
    """
    player = lambda stream: C.orion_location(stream, "Player",  # noqa: E731
                                             service="http://aftertouch.example:8000")
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s")]
    assert C.slots_to_write(C.parse_presets(_speaker_presets((1, "https://a.example.com/s"), form=player)), wanted) == []


def test_a_slot_in_the_legacy_form_counts_as_correct():
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s")]
    assert C.slots_to_write(C.parse_presets(_speaker_presets((1, "https://a.example.com/s"), form=_legacy)), wanted) == []


def test_relative_and_absolute_slots_side_by_side_both_count_as_correct():
    """The state every install passes through while it converts: some buttons each way."""
    absolute = lambda stream: C.orion_location(stream, "S", service=SERVICE)  # noqa: E731
    raw = _speaker_presets((1, "https://a.example.com/s")).replace("</presets>", "") + \
        _speaker_presets((2, "https://b.example.com/s"), form=absolute).replace("<presets>", "")
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s"),
              PresetEntry(button_number=2, name="B", location="https://b.example.com/s")]
    assert C.slots_to_write(C.parse_presets(raw), wanted) == []


def _registry(host: str = SERVICE, **override: str) -> str:
    """The shape AfterTouch's /bmx/registry/v1/services returns, trimmed to what is judged."""
    urls = {"TUNEIN": f"{host}/bmx/tunein",
            "LOCAL_INTERNET_RADIO": f"{host}{C.ORION_BASE_PATH}",
            "RADIO_BROWSER": "https://all.api.radio-browser.info/soundtouch", **override}
    return json.dumps({"bmx_services": [{"id": {"name": name, "value": 1}, "baseUrl": url}
                                        for name, url in urls.items()]})


def test_a_registry_advertising_the_service_itself_is_ok():
    assert C.registry_verdict(SERVICE, _registry()).verdict == RegistryVerdict.OK


def test_a_registry_advertising_another_host_is_foreign():
    """What a cloned container does: settings.json still names the machine it was copied from."""
    verdict = C.registry_verdict("http://192.0.2.20:8000", _registry())
    assert verdict.verdict == RegistryVerdict.FOREIGN
    assert (verdict.advertised or {})["LOCAL_INTERNET_RADIO"] == f"{SERVICE}{C.ORION_BASE_PATH}"


def test_one_foreign_entry_is_enough_to_be_foreign():
    body = _registry(TUNEIN="http://192.0.2.99:8000/bmx/tunein")
    assert C.registry_verdict(SERVICE, body).verdict == RegistryVerdict.FOREIGN


def test_a_trailing_slash_or_default_port_spelling_does_not_read_as_foreign():
    assert C.registry_verdict(SERVICE + "/", _registry()).verdict == RegistryVerdict.OK
    assert C.registry_verdict("http://svc.example", _registry("http://svc.example:80")).verdict == RegistryVerdict.OK


def test_a_registry_on_the_bose_cloud_is_dns_mode_not_foreign():
    """In AfterTouch's DNS mode the registry names content.api.bose.io on purpose."""
    body = _registry("https://content.api.bose.io")
    assert C.registry_verdict(SERVICE, body).verdict == RegistryVerdict.DNS_MODE


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"bmx_services": []}',
                                  '{"bmx_services": [{"id": {"name": "TUNEIN"}}]}'])
def test_an_unreadable_registry_says_so_rather_than_ok(body):
    assert C.registry_verdict(SERVICE, body).verdict == RegistryVerdict.UNREADABLE


def _raw_presets(*items: tuple[int, str, str]) -> str:
    """/presets as the speaker returns it, art and account attributes included."""
    return "<presets>" + "".join(
        f'<preset id="{button}" createdOn="1" updatedOn="2"><ContentItem source="{source}" '
        f'type="stationurl" location="{location.replace("&", "&amp;")}" sourceAccount="" '
        f'isPresetable="true"><itemName>Name {button} &amp; Co</itemName>'
        f'<containerArt>https://img.example.com/{button}.png</containerArt></ContentItem></preset>'
        for button, source, location in items) + "</presets>"


def test_relativize_plan_rewrites_only_host_bound_radio_slots():
    absolute = C.orion_location("https://a.example.com/s?x=1&y=2", "A", service=SERVICE)
    raw = _raw_presets((1, "LOCAL_INTERNET_RADIO", absolute),
                       (2, "LOCAL_INTERNET_RADIO", C.orion_location("https://b.example.com/s", "B")),
                       (3, "LOCAL_INTERNET_RADIO", _legacy("https://c.example.com/s")),
                       (4, "TUNEIN", "/v1/playback/station/s12345"),
                       (5, "TUNEIN", _legacy("https://d.example.com/s")))
    plan = C.relativize_plan(raw)
    assert [step.button for step in plan] == [1, 3]
    assert plan[0].old == absolute
    assert plan[0].new == C.orion_location("https://a.example.com/s?x=1&y=2", "A")


def test_relativize_plan_turns_a_legacy_playback_slot_into_the_relative_orion_form():
    """The player's older catalog entry still writes this host-bound form (gesellix #784).

    The stream sits in the base64, so nothing is lost: the new blob carries the slot's own name
    and art, which is what the speaker shows for the button.
    """
    stream = "https://c.example.com/s?x=1&y=2"
    plan = C.relativize_plan(_raw_presets((3, "LOCAL_INTERNET_RADIO", _legacy(stream))))
    assert [step.button for step in plan] == [3]
    assert plan[0].new == C.orion_location(stream, "Name 3 & Co",
                                              image_url="https://img.example.com/3.png")
    assert C.stream_url_from_location(plan[0].new) == stream
    assert SERVICE not in plan[0].body


def test_relativize_plan_names_a_legacy_slot_from_its_location_when_the_item_has_none():
    location = _legacy("https://c.example.com/s").replace("?name=S", "?name=Radio+%26+Co")
    raw = (f'<presets><preset id="2"><ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
           f'location="{location}" /></preset></presets>')
    plan = C.relativize_plan(raw)
    assert plan[0].new == C.orion_location("https://c.example.com/s", "Radio & Co")


def test_relativize_plan_leaves_an_undecodable_legacy_slot_alone():
    """Rewriting what cannot be read back would turn a working button into an empty station."""
    raw = _raw_presets((3, "LOCAL_INTERNET_RADIO", f"{SERVICE}{C.PLAYBACK_PATH}%%%?name=S"))
    assert C.relativize_plan(raw) == []


def test_relativize_plan_keeps_everything_but_the_location():
    """Same name, art, source and type: the quick fix upstream offers, done from here."""
    absolute = C.orion_location("https://a.example.com/s?x=1&y=2", "A", service=SERVICE)
    body = C.relativize_plan(_raw_presets((5, "LOCAL_INTERNET_RADIO", absolute)))[0].body
    assert body.startswith('<preset id="5">') and body.endswith("</preset>")
    assert "<itemName>Name 5 &amp; Co</itemName>" in body
    assert "<containerArt>https://img.example.com/5.png</containerArt>" in body
    assert 'source="LOCAL_INTERNET_RADIO"' in body and 'type="stationurl"' in body
    relative = C.orion_location("https://a.example.com/s?x=1&y=2", "A")
    assert f'location="{relative.replace("&", "&amp;")}"' in body
    assert "createdOn" not in body and SERVICE not in body


def test_relativize_plan_is_empty_when_everything_is_already_relative():
    raw = _raw_presets((1, "LOCAL_INTERNET_RADIO", C.orion_location("https://a.example.com/s", "A")))
    assert C.relativize_plan(raw) == []


def test_a_kept_entry_is_never_written():
    """A Spotify or library preset the skill does not manage must not be judged as missing."""
    wanted = [PresetEntry(button_number=2, name="Album", location="spotify:album:x", keep=True,
                          source="SPOTIFY", content_item_type="tracklisturl")]
    assert C.slots_to_write(C.parse_presets(_speaker_presets()), wanted) == []


def test_slots_to_write_compares_by_stream_not_by_count():
    """A slot pointing at a station the owner replaced must read as needing a write."""
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s"),
              PresetEntry(button_number=2, name="B", location="https://b.example.com/s")]
    have = _speaker_presets((1, "https://a.example.com/s"), (2, "https://old.example.com/s"))
    assert C.missing_streams(C.parse_presets(have), wanted) == ["https://b.example.com/s"]


def test_slots_to_write_is_empty_when_every_button_is_right():
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s")]
    assert C.slots_to_write(C.parse_presets(_speaker_presets((1, "https://a.example.com/s"))), wanted) == []


def test_the_right_station_on_the_wrong_button_still_needs_writing():
    """Comparing streams alone calls this correct, so the button has to be part of the key."""
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s")]
    have = _speaker_presets((3, "https://a.example.com/s"))
    assert C.missing_streams(C.parse_presets(have), wanted) == ["https://a.example.com/s"]


def test_two_buttons_may_hold_the_same_station():
    """A duplicate is a legitimate template, and each slot is judged on its own."""
    wanted = [PresetEntry(button_number=1, name="A", location="https://a.example.com/s"),
              PresetEntry(button_number=2, name="A", location="https://a.example.com/s")]
    have = _speaker_presets((1, "https://a.example.com/s"))
    assert C.missing_streams(C.parse_presets(have), wanted) == ["https://a.example.com/s"]


def test_parse_preset_slots_keys_by_button():
    slots = C.parse_presets(_speaker_presets((1, "https://a.example.com/s"),
                                             (4, "https://b.example.com/s"))).slots()
    assert sorted(slots) == [1, 4]


def test_parse_preset_slots_skips_a_slot_with_no_location():
    """An empty button is absent, never a slot holding the empty string."""
    assert C.parse_presets('<preset id="2"></preset>').slots() == {}


class _FakeSocket:
    """A socket that hands back a fixed script of chunks, then times out.

    _read_to_prompt takes the socket, so this substitutes at a real seam rather than patching the
    module's internals.
    """

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)

    def settimeout(self, _timeout: float) -> None:
        return None

    def recv(self, _size: int) -> bytes:
        if not self._chunks:
            raise TimeoutError
        return self._chunks.pop(0)


def test_a_reply_ending_at_the_prompt_is_complete():
    text, complete = C._read_to_prompt(_FakeSocket(b"margeServerUrl {\n", b"}\n-> "), timeout=1)
    assert complete is True and "margeServerUrl" in text


def test_a_reply_that_never_reaches_the_prompt_is_marked_incomplete():
    """The text still comes back, so only the flag separates a truncated read from a finished one."""
    text, complete = C._read_to_prompt(_FakeSocket(b"margeServerUrl {\n"), timeout=1)
    assert complete is False and "margeServerUrl" in text


def test_parse_presets_reads_every_location():
    assert len(C.parse_presets('<ContentItem location="a" /><ContentItem location="b" />').locations) == 2


def test_http_get_refuses_a_non_http_scheme():
    """A file: URL would read local files, so the scheme is checked before the request."""
    try:
        C.http_get("file:///etc/passwd")
    except C.SpeakerError as exc:
        assert "non-http" in str(exc)
    else:
        raise AssertionError("file: URL was not refused")


def test_the_default_enable_ssh_form_is_persistence_only():
    """The field-confirmed form writes through envswitch alone and needs no reboot."""
    cmds = C.build_enable_ssh_commands("http://192.0.2.10:8000")
    assert len(cmds) == 1
    assert cmds[0].startswith("envswitch boseurls set")
    assert C.SSH_INJECT in cmds[0]


def test_the_full_config_form_also_rides_the_runtime_key_and_reboots():
    """Those two differences are what upstream reports as mattering on the devices that need it."""
    cmds = C.build_enable_ssh_commands("http://192.0.2.10:8000", full_config=True)
    marge = [c for c in cmds if c.startswith("sys configuration margeServerUrl")][0]
    assert C.SSH_INJECT in marge
    assert any(c.startswith("envswitch boseurls set") and C.SSH_INJECT in c for c in cmds)
    assert cmds[-1] == "sys reboot"


def test_the_injection_never_lands_on_the_other_url_fields():
    """Shell text on bmxRegistryUrl or statsServerUrl would persist with nothing to clean it up."""
    cmds = C.build_enable_ssh_commands("http://192.0.2.10:8000", full_config=True)
    for field in ("bmxRegistryUrl", "statsServerUrl", "swUpdateUrl"):
        line = [c for c in cmds if c.startswith(f"sys configuration {field}")][0]
        assert C.SSH_INJECT not in line


def test_enable_ssh_and_migrate_disagree_about_the_marge_url_by_exactly_the_injection():
    """migrate is what cleans up after enable-ssh, so the two must differ only by the suffix."""
    clean = [c for c in C.build_url_commands("http://192.0.2.10:8000")
             if c.startswith("envswitch boseurls set")][0]
    dirty = C.build_enable_ssh_commands("http://192.0.2.10:8000")[0]
    assert dirty.replace(C.SSH_INJECT, "") == clean


# A speaker's own web server puts its system clock in the Date header of every response, which is
# the only way to read the clock of a box whose SSH is closed. Both headers below are real shapes:
# the box renders its LOCAL time and labels it GMT, so a correct clock can read a whole UTC offset
# out. NOW is the true epoch of the moment the first header was captured.
HEADER_NOW = "Sun, 20 Sep 2026 03:20:33 GMT"
NOW = 1789867233
HEADER_2015 = "Mon, 06 Jul 2015 20:36:50 GMT"


def test_a_clock_within_the_tolerance_reads_ok_and_reports_what_the_box_said():
    state = C.clock_state(HEADER_NOW, now=NOW)
    assert state.verdict == ClockVerdict.OK
    assert state.reading == "2026-09-20 03:20:33"


def test_a_local_time_offset_is_not_mistaken_for_a_wrong_clock():
    """The header labels local time as GMT, so a correct clock is hours out by construction."""
    assert C.clock_state(HEADER_NOW, now=NOW - 7200).verdict == ClockVerdict.OK
    assert C.clock_state(HEADER_NOW, now=NOW + 7200).verdict == ClockVerdict.OK


def test_a_clock_left_in_2015_reads_wrong():
    state = C.clock_state(HEADER_2015, now=NOW)
    assert state.verdict == ClockVerdict.WRONG
    assert state.reading == "2015-07-06 20:36:50"


def test_the_tolerance_clears_every_utc_offset_and_still_catches_the_real_fault():
    """No offset on earth reaches a day, and the fault being caught is eleven years."""
    assert C.CLOCK_TOLERANCE_S > 15 * 3600


def test_no_header_or_an_unreadable_one_reads_unknown():
    for header in (None, "", "whenever it feels like"):
        state = C.clock_state(header, now=NOW)
        assert state.verdict == ClockVerdict.UNKNOWN
        assert state.reading is None


@pytest.mark.parametrize("service", ["http://127.0.0.1:8000", "http://localhost:8000",
                                     "http://127.0.1.1:8000", "http://[::1]:8000"])
def test_a_loopback_service_address_cannot_judge_the_registry(service):
    """Run on the service's own machine, the address given names no host a speaker could use.

    Comparing against it called a correct registry foreign; not knowing is not a fault.
    """
    verdict = C.registry_verdict(service, _registry())
    assert verdict.verdict == RegistryVerdict.UNJUDGED
    assert "speakers" in verdict.reason


def test_a_speaker_that_never_lists_radio_browser_can_still_be_ready():
    """Firmware 20 (the Wireless Link Adapter) publishes no RADIO_BROWSER at all, so a source the
    speaker never listed cannot be a source that is still loading."""
    assert C.parse_sources(SOURCES).radio_ready() is True


def test_a_listed_source_that_is_still_loading_is_not_ready():
    payload = SOURCES.replace('source="TUNEIN" status="READY"', 'source="TUNEIN" status="UNAVAILABLE"')
    assert C.parse_sources(payload).radio_ready() is False


def test_without_local_internet_radio_the_radio_is_not_ready():
    """Every preset this skill writes plays through LOCAL_INTERNET_RADIO, so its absence is not
    excused the way an unlisted optional source is."""
    payload = SOURCES.replace('<sourceItem source="LOCAL_INTERNET_RADIO" status="READY" isLocal="false" />', "")
    assert C.parse_sources(payload).radio_ready() is False


def test_a_speaker_listing_no_radio_source_is_not_ready():
    assert C.parse_sources('<sources deviceID="00005E005300"></sources>').radio_ready() is False
    assert C.RadioSources({}).radio_ready() is False
