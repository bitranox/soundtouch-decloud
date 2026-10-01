"""Tests for template validation and the preset body that gets written."""
import json
import pytest
import soundtouch_core as C
import soundtouch_presets as P

GOOD = {"deviceId": "00005E005300", "name": "Example Speaker",
        "presets": [{"buttonNumber": 1, "name": "Example Radio",
                     "location": "https://radio.example.com/stream",
                     "contentItemType": "stationurl", "source": "LOCAL_INTERNET_RADIO"}]}


def _write(tmp_path, data):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_a_good_template_loads(tmp_path):
    assert len(P.load_template(_write(tmp_path, GOOD))["presets"]) == 1


def test_a_template_with_no_presets_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no presets"):
        P.load_template(_write(tmp_path, {"presets": []}))


def test_a_missing_field_is_refused(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    del bad["presets"][0]["location"]
    with pytest.raises(ValueError, match="location"):
        P.load_template(_write(tmp_path, bad))


def test_a_duplicate_button_is_refused(tmp_path):
    """Two entries on one button silently means one of them is never written."""
    bad = json.loads(json.dumps(GOOD))
    bad["presets"].append(dict(bad["presets"][0]))
    with pytest.raises(ValueError, match="twice"):
        P.load_template(_write(tmp_path, bad))


@pytest.mark.parametrize("button", [0, 7, "1", None])
def test_a_button_outside_one_to_six_is_refused(tmp_path, button):
    bad = json.loads(json.dumps(GOOD))
    bad["presets"][0]["buttonNumber"] = button
    with pytest.raises(ValueError, match="buttonNumber"):
        P.load_template(_write(tmp_path, bad))


@pytest.mark.parametrize("wrapped", [
    "http://192.0.2.10:8000/custom/v1/playback/abc?name=x",
    "http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data=abc",
    "/station?data=abc",
])
def test_an_already_wrapped_location_is_refused(tmp_path, wrapped):
    """The script adds the adapter wrapping, so a pre-wrapped location would be double-wrapped."""
    bad = json.loads(json.dumps(GOOD))
    bad["presets"][0]["location"] = wrapped
    with pytest.raises(ValueError, match="PLAIN stream URL"):
        P.load_template(_write(tmp_path, bad))


KEPT = {"buttonNumber": 2, "name": "An Album", "location": "/playback/container/c3BvdGlmeTphbGJ1bTox",
        "contentItemType": "tracklisturl", "source": "SPOTIFY", "keep": True}


def test_a_kept_entry_loads_without_a_stream_url(tmp_path):
    """A Spotify or library preset is not radio; demanding an http stream for it is the bug."""
    data = dict(GOOD, presets=[GOOD["presets"][0], KEPT])
    assert len(P.load_template(_write(tmp_path, data))["presets"]) == 2


def test_validate_reports_a_kept_entry_as_kept_not_unplayable(tmp_path, capsys):
    """Nothing is fetched for it: an album location is not a URL, and fetching it would read dead."""
    assert P.main(["validate", "--template", _write(tmp_path, dict(GOOD, presets=[KEPT]))]) == 0
    result = json.loads(capsys.readouterr().out)["data"]["results"][0]
    assert (result["verdict"], result["name"]) == ("kept", "An Album")


def test_preset_xml_wraps_the_location_as_the_player_does():
    """A raw stream URL here is accepted by the speaker and never plays. The wrapping is the
    relative Orion form AfterTouch's own player and CLI write since v0.138.0, so there is one
    format on the account and the preset follows the service to a new address."""
    xml = P.preset_xml(GOOD["presets"][0])
    assert 'location="/station?data=' in xml
    assert "192.0.2.10" not in xml
    assert 'location="https://radio.example.com/stream"' not in xml


def test_preset_xml_writes_the_absolute_form_only_when_given_a_service():
    """The fallback for firmware that cannot resolve a relative location."""
    xml = P.preset_xml(GOOD["presets"][0], service="http://192.0.2.10:8000")
    assert 'location="http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data=' in xml


def test_preset_xml_escapes_the_station_name():
    entry = dict(GOOD["presets"][0], name="Rock & Roll <FM>")
    xml = P.preset_xml(entry)
    assert "<itemName>Rock &amp; Roll &lt;FM&gt;</itemName>" in xml


def test_preset_xml_carries_the_source_and_type():
    xml = P.preset_xml(GOOD["presets"][0])
    assert 'source="LOCAL_INTERNET_RADIO"' in xml and 'type="stationurl"' in xml


def test_radio_ready_is_false_when_the_speaker_cannot_be_reached(monkeypatch):
    """Unreachable must read as not-ready, so a restore never writes into the wipe window."""
    def boom(_url, timeout=8.0):
        raise P.SpeakerError("unreachable")
    monkeypatch.setattr(P, "http_get", boom)
    assert P.radio_ready("192.0.2.31") is False


def test_radio_ready_is_true_only_when_the_radio_source_is_mounted(monkeypatch):
    monkeypatch.setattr(P, "http_get", lambda *a, **k:
                        '<sourceItem source="LOCAL_INTERNET_RADIO" status="READY" />')
    assert P.radio_ready("192.0.2.31") is True
    monkeypatch.setattr(P, "http_get", lambda *a, **k:
                        '<sourceItem source="LOCAL_INTERNET_RADIO" status="UNAVAILABLE" />')
    assert P.radio_ready("192.0.2.31") is False


ABSOLUTE = C.orion_location("https://a.example.com/s?x=1&y=2", "A", service="http://192.0.2.10:8000")


def _presets_xml(location: str) -> str:
    return (f'<presets><preset id="3"><ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
            f'location="{location.replace("&", "&amp;")}" sourceAccount="" isPresetable="true">'
            f"<itemName>A</itemName></ContentItem></preset></presets>")


class FakeSpeaker:
    """A speaker at the HTTP edge: /presets reflects what storePreset last wrote."""

    def __init__(self, location: str, radio: str = "READY") -> None:
        self.location = location
        self.radio = radio
        self.stored: list[str] = []

    def get(self, url: str, timeout: float = 8.0) -> str:
        if url.endswith("/sources"):
            return f'<sourceItem source="LOCAL_INTERNET_RADIO" status="{self.radio}" />'
        return _presets_xml(self.location)

    def post(self, ip: str, body: str) -> None:
        self.stored.append(body)
        self.location = body.split('location="', 1)[1].split('"', 1)[0].replace("&amp;", "&")


def _fake(monkeypatch, speaker: FakeSpeaker) -> None:
    monkeypatch.setattr(P, "http_get", speaker.get)
    monkeypatch.setattr(P, "_post_preset", speaker.post)


def test_relativize_without_confirm_only_reports(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker(ABSOLUTE)
    _fake(monkeypatch, speaker)
    rc = P.main(["relativize", "--ip", "192.0.2.31", "--outdir", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)["data"]
    assert rc == 1 and speaker.stored == []
    assert data["would_rewrite"] == [3]


def test_relativize_backs_up_then_rewrites_to_the_relative_form(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker(ABSOLUTE)
    _fake(monkeypatch, speaker)
    rc = P.main(["relativize", "--ip", "192.0.2.31", "--outdir", str(tmp_path), "--confirm"])
    data = json.loads(capsys.readouterr().out)["data"]
    assert rc == 0, data
    assert speaker.location == C.orion_location("https://a.example.com/s?x=1&y=2", "A")
    assert data["rewrote"] == [3]
    assert any(p.name.endswith("-presets.xml") for p in tmp_path.iterdir())


def test_relativize_is_a_no_op_when_everything_is_relative(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker(C.orion_location("https://a.example.com/s", "A"))
    _fake(monkeypatch, speaker)
    rc = P.main(["relativize", "--ip", "192.0.2.31", "--outdir", str(tmp_path), "--confirm"])
    assert rc == 0 and speaker.stored == []
    assert json.loads(capsys.readouterr().out)["data"]["rewrote"] == []


def test_relativize_refuses_to_write_before_the_radio_source_is_mounted(monkeypatch, tmp_path, capsys):
    """A write in the boot window is silently undone, as for restore."""
    speaker = FakeSpeaker(ABSOLUTE, radio="UNAVAILABLE")
    _fake(monkeypatch, speaker)
    rc = P.main(["relativize", "--ip", "192.0.2.31", "--outdir", str(tmp_path), "--confirm"])
    assert rc == 1 and speaker.stored == []
    assert "mounted" in json.loads(capsys.readouterr().out)["data"]["error"]


LEGACY_STREAM = "https://radio.example.com/stream"
LEGACY = (f"http://192.0.2.10:8000{C.PLAYBACK_PATH}"
          f"{__import__('base64').urlsafe_b64encode(LEGACY_STREAM.encode()).decode()}?name=A")


def test_relativize_rewrites_a_legacy_playback_slot(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker(LEGACY)
    _fake(monkeypatch, speaker)
    rc = P.main(["relativize", "--ip", "192.0.2.31", "--outdir", str(tmp_path), "--confirm"])
    data = json.loads(capsys.readouterr().out)["data"]
    assert rc == 0, data
    assert data["rewrote"] == [3]
    assert speaker.location == C.orion_location(LEGACY_STREAM, "A")


def _check(monkeypatch, tmp_path, capsys, location: str) -> tuple[int, dict]:
    _fake(monkeypatch, FakeSpeaker(location))
    template = dict(GOOD, presets=[dict(GOOD["presets"][0], buttonNumber=3)])
    rc = P.main(["check", "--ip", "192.0.2.31", "--template", _write(tmp_path, template)])
    return rc, json.loads(capsys.readouterr().out)["data"]


@pytest.mark.parametrize("location", [
    LEGACY, C.orion_location(LEGACY_STREAM, "A", service="http://192.0.2.10:8000")])
def test_check_warns_on_a_host_bound_slot_and_still_passes(monkeypatch, tmp_path, capsys, location):
    """The station is right, so the check passes; the host in the location is what is wrong."""
    rc, data = _check(monkeypatch, tmp_path, capsys, location)
    assert rc == 0, data
    assert data["host_bound"] == [3]
    assert "relativize" in data["warning"]


def test_restore_says_already_correct_but_names_a_host_bound_slot(monkeypatch, tmp_path, capsys):
    """restore compares streams, so it writes nothing here; it must still say what it saw."""
    _fake(monkeypatch, FakeSpeaker(LEGACY))
    template = dict(GOOD, presets=[dict(GOOD["presets"][0], buttonNumber=3)])
    rc = P.main(["restore", "--ip", "192.0.2.31", "--template", _write(tmp_path, template),
                 "--confirm"])
    data = json.loads(capsys.readouterr().out)["data"]
    assert rc == 0 and data["wrote"] == 0
    assert data["host_bound"] == [3] and "relativize" in data["warning"]


def test_check_has_no_warning_for_a_relative_slot(monkeypatch, tmp_path, capsys):
    rc, data = _check(monkeypatch, tmp_path, capsys, C.orion_location(LEGACY_STREAM, "A"))
    assert rc == 0, data
    assert data["host_bound"] == [] and "warning" not in data


def test_restore_writes_the_relative_form(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker("https://old.example.com/gone")
    _fake(monkeypatch, speaker)
    template = dict(GOOD, presets=[dict(GOOD["presets"][0], buttonNumber=3)])
    rc = P.main(["restore", "--ip", "192.0.2.31", "--template", _write(tmp_path, template),
                 "--confirm"])
    assert rc == 0, capsys.readouterr().out
    assert speaker.location.startswith("/station?data=")


def test_restore_absolute_needs_a_service(tmp_path, capsys):
    rc = P.main(["restore", "--ip", "192.0.2.31", "--template", _write(tmp_path, GOOD),
                 "--absolute", "--confirm"])
    assert rc == 2 and "--service" in json.loads(capsys.readouterr().out)["data"]["error"]


def test_restore_absolute_writes_the_service_host(monkeypatch, tmp_path, capsys):
    speaker = FakeSpeaker("https://old.example.com/gone")
    _fake(monkeypatch, speaker)
    template = dict(GOOD, presets=[dict(GOOD["presets"][0], buttonNumber=3)])
    rc = P.main(["restore", "--ip", "192.0.2.31", "--template", _write(tmp_path, template),
                 "--service", "http://192.0.2.10:8000", "--absolute", "--confirm"])
    assert rc == 0, capsys.readouterr().out
    assert speaker.location.startswith("http://192.0.2.10:8000/core02/")
