"""Tests for template validation and the preset body that gets written."""
import json
import pytest
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
    Orion form AfterTouch's own player and CLI write, so there is one format on the account."""
    xml = P.preset_xml("http://192.0.2.10:8000", GOOD["presets"][0])
    assert 'location="http://192.0.2.10:8000/core02/svc-bmx-adapter-orion/prod/orion/station?data=' in xml
    assert 'location="https://radio.example.com/stream"' not in xml


def test_preset_xml_escapes_the_station_name():
    entry = dict(GOOD["presets"][0], name="Rock & Roll <FM>")
    xml = P.preset_xml("http://192.0.2.10:8000", entry)
    assert "<itemName>Rock &amp; Roll &lt;FM&gt;</itemName>" in xml


def test_preset_xml_carries_the_source_and_type():
    xml = P.preset_xml("http://192.0.2.10:8000", GOOD["presets"][0])
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
