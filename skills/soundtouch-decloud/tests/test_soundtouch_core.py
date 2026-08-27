"""Tests for the parsing and command-building rules.

Every case here is a real reading that a plausible implementation gets wrong, so each test is
written against the payload shape the speaker actually returns rather than a tidied-up sample.
"""
import soundtouch_core as C

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
    assert urls["margeServerUrl"] == "http://192.0.2.10:8000"
    assert urls["bmxRegistryUrl"] == "https://content.api.bose.io/bmx/registry/v1/services"
    assert len(urls) == 4


def test_parse_urls_ignores_a_field_with_no_value():
    assert "margeServerUrl" not in C.parse_urls("margeServerUrl {\n}\n->")


def test_parse_urls_of_empty_input_is_empty():
    assert C.parse_urls("") == {}


def test_cloud_leftovers_flags_every_bose_domain():
    """Clearing only bose.com leaves bmxRegistryUrl on bose.io, and radio never mounts."""
    left = C.cloud_leftovers(C.parse_urls(GETPDO))
    assert set(left) == {"statsServerUrl", "bmxRegistryUrl"}


def test_cloud_leftovers_empty_when_fully_local():
    assert C.cloud_leftovers(C.service_urls("http://192.0.2.10:8000")) == {}


def test_injected_values_spots_leftover_shell_text():
    urls = {"margeServerUrl": "http://192.0.2.10:8000;touch /tmp/x", "swUpdateUrl": "http://192.0.2.10:8000"}
    assert list(C.injected_values(urls)) == ["margeServerUrl"]


def test_parse_sources_matches_the_attribute_not_a_label():
    got = C.parse_sources(SOURCES)
    assert got["TUNEIN"] == "READY"
    assert got["LOCAL_INTERNET_RADIO"] == "READY"


def test_parse_sources_reports_a_missing_source_as_absent():
    """A source the speaker never published must not read as READY."""
    assert C.parse_sources(SOURCES)["RADIO_BROWSER"] == "ABSENT"


def test_parse_sources_keeps_a_non_ready_status():
    payload = SOURCES.replace('source="TUNEIN" status="READY"', 'source="TUNEIN" status="UNAVAILABLE"')
    assert C.parse_sources(payload)["TUNEIN"] == "UNAVAILABLE"


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
    assert C.service_urls("http://192.0.2.10:8000/")["swUpdateUrl"].count("//") == 1


def test_playback_location_round_trips():
    """URL-safe base64 WITH padding, matching what the service builds."""
    stream = "https://radio.example.com/stream?x=1&y=2"
    loc = C.playback_location("http://192.0.2.10:8000", stream, "Example Radio")
    assert C.PLAYBACK_PATH in loc
    assert C.decode_playback_location(loc) == stream


def test_playback_location_is_url_safe():
    loc = C.playback_location("http://192.0.2.10:8000", "https://radio.example.com/a?b=1", "N")
    encoded = loc.split(C.PLAYBACK_PATH, 1)[1].split("?", 1)[0]
    assert "+" not in encoded and "/" not in encoded


def test_decode_ignores_a_raw_stream_url():
    assert C.decode_playback_location("https://radio.example.com/stream") == ""


SERVICE = "http://192.0.2.10:8000"


def _speaker_presets(*slots: tuple[int, str]) -> str:
    """The shape /presets really returns: each ContentItem inside a <preset id="N"> wrapper."""
    return "<presets>" + "".join(
        f'<preset id="{button}"><ContentItem '
        f'location="{C.playback_location(SERVICE, stream, "S")}" /></preset>'
        for button, stream in slots) + "</presets>"


def test_slots_to_write_compares_by_stream_not_by_count():
    """A slot pointing at a station the owner replaced must read as needing a write."""
    wanted = [{"buttonNumber": 1, "name": "A", "location": "https://a.example.com/s"},
              {"buttonNumber": 2, "name": "B", "location": "https://b.example.com/s"}]
    have = _speaker_presets((1, "https://a.example.com/s"), (2, "https://old.example.com/s"))
    assert C.missing_streams(have, wanted) == ["https://b.example.com/s"]


def test_slots_to_write_is_empty_when_every_button_is_right():
    wanted = [{"buttonNumber": 1, "name": "A", "location": "https://a.example.com/s"}]
    assert C.slots_to_write(_speaker_presets((1, "https://a.example.com/s")), wanted) == []


def test_the_right_station_on_the_wrong_button_still_needs_writing():
    """Comparing streams alone calls this correct, so the button has to be part of the key."""
    wanted = [{"buttonNumber": 1, "name": "A", "location": "https://a.example.com/s"}]
    have = _speaker_presets((3, "https://a.example.com/s"))
    assert C.missing_streams(have, wanted) == ["https://a.example.com/s"]


def test_two_buttons_may_hold_the_same_station():
    """A duplicate is a legitimate template, and each slot is judged on its own."""
    wanted = [{"buttonNumber": 1, "name": "A", "location": "https://a.example.com/s"},
              {"buttonNumber": 2, "name": "A", "location": "https://a.example.com/s"}]
    have = _speaker_presets((1, "https://a.example.com/s"))
    assert C.missing_streams(have, wanted) == ["https://a.example.com/s"]


def test_parse_preset_slots_keys_by_button():
    slots = C.parse_preset_slots(_speaker_presets((1, "https://a.example.com/s"),
                                                  (4, "https://b.example.com/s")))
    assert sorted(slots) == [1, 4]


def test_parse_preset_slots_skips_a_slot_with_no_location():
    """An empty button is absent, never a slot holding the empty string."""
    assert C.parse_preset_slots('<preset id="2"></preset>') == {}


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
    assert len(C.parse_presets('<ContentItem location="a" /><ContentItem location="b" />')) == 2


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
