"""Tests for the migration verdict and the reboot proof."""
import json

import soundtouch_onboard as O
from soundtouch_core import ServiceUrls, TelnetReply, UrlField

LOCAL = {UrlField.MARGE: "http://192.0.2.10:8000",
         UrlField.STATS: "http://192.0.2.10:8000",
         UrlField.SW_UPDATE: "http://192.0.2.10:8000/updates/soundtouch",
         UrlField.BMX_REGISTRY: "http://192.0.2.10:8000/bmx/registry/v1/services"}


def _urls(**changes: str | None) -> ServiceUrls:
    """The fully local speaker with fields replaced; a value of None drops the field."""
    merged = dict(LOCAL)
    for name, value in changes.items():
        field = UrlField(name)
        if value is None:
            del merged[field]
        else:
            merged[field] = value
    return ServiceUrls(merged)


def test_a_fully_local_speaker_passes():
    assert O.migration_verdict(_urls()).ok is True


def test_one_cloud_url_fails_even_though_the_others_are_local():
    """This is the case that looks migrated and plays nothing."""
    urls = _urls(bmxRegistryUrl="https://content.api.bose.io/bmx/registry/v1/services")
    verdict = O.migration_verdict(urls)
    assert verdict.ok is False
    assert "bmxRegistryUrl" in verdict.to_json()["cloud_leftovers"]


def test_leftover_injection_fails_even_when_no_cloud_url_remains():
    urls = _urls(margeServerUrl="http://192.0.2.10:8000;touch /tmp/remote_services")
    verdict = O.migration_verdict(urls)
    assert verdict.ok is False
    assert "margeServerUrl" in verdict.to_json()["still_injected"]


def test_a_missing_field_fails():
    verdict = O.migration_verdict(_urls(statsServerUrl=None))
    assert verdict.ok is False
    assert verdict.to_json()["missing"] == ["statsServerUrl"]


def test_an_empty_read_is_not_a_pass():
    """Reading nothing back must never look like a clean speaker."""
    assert O.migration_verdict(ServiceUrls({})).ok is False


def test_wait_down_returns_none_when_the_speaker_never_drops(monkeypatch):
    """A wait that only checks for 'back up' reports success when the reboot never happened."""
    monkeypatch.setattr(O, "port_open", lambda *a, **k: True)
    monkeypatch.setattr(O.time, "sleep", lambda _s: None)
    assert O.wait_down("192.0.2.31", limit=0.3) is None


def test_wait_down_measures_the_drop(monkeypatch):
    monkeypatch.setattr(O, "port_open", lambda *a, **k: False)
    assert O.wait_down("192.0.2.31", limit=5) is not None


def test_wait_up_returns_none_when_it_never_comes_back(monkeypatch):
    monkeypatch.setattr(O, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(O.time, "sleep", lambda _s: None)
    assert O.wait_up("192.0.2.31", limit=0.3) is None


class FakeBox:
    """A speaker at its HTTP edge: a volume a reboot resets, and a now_playing a key changes."""

    def __init__(self, volume: int = 41, source: str = "STANDBY") -> None:
        self.volume = volume
        self.source = source
        self.keys: list[str] = []
        self.station = ""

    def get(self, url: str, timeout: float = 8.0) -> str:
        if url.endswith("/volume"):
            return f"<volume><targetvolume>{self.volume}</targetvolume><actualvolume>{self.volume}</actualvolume></volume>"
        if url.endswith("/sources"):
            return "".join(f'<sourceItem source="{s}" status="READY" />'
                           for s in ("TUNEIN", "LOCAL_INTERNET_RADIO", "RADIO_BROWSER"))
        return (f'<nowPlaying source="{self.source}"><itemName>{self.station}</itemName>'
                f"<playStatus>PLAY_STATE</playStatus></nowPlaying>")

    def post(self, ip: str, path: str, body: str) -> None:
        if path == "volume":
            self.volume = int(body.split(">", 1)[1].split("<", 1)[0])

    def key(self, ip: str, name: str) -> None:
        self.keys.append(name)
        if name == "POWER":
            self.source, self.station = "LOCAL_INTERNET_RADIO", "Last Station"
        elif name.startswith("PRESET_") and self.source != "STANDBY":
            self.station = "Example Radio"
        elif name.startswith("PRESET_"):
            # What the firmware does: a preset pressed in standby only wakes it onto its last station.
            self.source, self.station = "LOCAL_INTERNET_RADIO", "Last Station"


def _reset_volume(box: FakeBox) -> list[TelnetReply]:
    """What a reboot command does to the box: the volume falls back to the factory 10."""
    box.volume = 10
    return []


def _box(monkeypatch, box: FakeBox, *, back: bool = True) -> None:
    monkeypatch.setattr(O, "http_get", box.get)
    monkeypatch.setattr(O, "_post", box.post)
    monkeypatch.setattr(O, "_key", box.key)
    monkeypatch.setattr(O, "telnet_run", lambda _ip, _cmds: _reset_volume(box))
    monkeypatch.setattr(O, "wait_down", lambda *_a, **_k: 4.0)
    monkeypatch.setattr(O, "wait_up", lambda *_a, **_k: 60.0 if back else None)
    monkeypatch.setattr(O.time, "sleep", lambda _s: None)


def test_a_reboot_puts_the_volume_back(monkeypatch, capsys):
    """Measured on two ST20s on 27.0.6: they came back from a reboot at volume 10, not their 41."""
    box = FakeBox(volume=41)
    _box(monkeypatch, box)
    assert O.main(["--ip", "192.0.2.31", "reboot", "--confirm"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert box.volume == 41
    assert (data["volume_before"], data["volume_after"]) == (41, 41)


def test_a_speaker_that_never_comes_back_is_pointed_at_its_new_address(monkeypatch, capsys):
    """Measured: a speaker on plain DHCP came back on the next address, and the wait timed out."""
    _box(monkeypatch, FakeBox(), back=False)
    assert O.main(["--ip", "192.0.2.31", "reboot", "--confirm"]) == 1
    data = json.loads(capsys.readouterr().out)["data"]
    assert "soundtouch_find.py" in data["next"] and "DHCP" in data["next"]


def test_play_wakes_a_sleeping_speaker_before_pressing_the_preset(monkeypatch, capsys):
    """Pressed in standby the preset only wakes the box onto its last station, which is not a proof."""
    box = FakeBox(source="STANDBY")
    _box(monkeypatch, box)
    rc = O.main(["--ip", "192.0.2.31", "play", "--preset", "2", "--expect", "Example Radio",
                 "--confirm", "--wait", "5"])
    assert box.keys == ["POWER", "PRESET_2"]
    assert rc == 0, capsys.readouterr().out


def test_play_on_an_awake_speaker_presses_only_the_preset(monkeypatch, capsys):
    box = FakeBox(source="LOCAL_INTERNET_RADIO")
    _box(monkeypatch, box)
    O.main(["--ip", "192.0.2.31", "play", "--preset", "2", "--expect", "Example Radio",
            "--confirm", "--wait", "5"])
    assert box.keys == ["PRESET_2"]


def test_an_unreadable_volume_before_the_reboot_does_not_fail_it(monkeypatch, capsys):
    box = FakeBox(volume=41)
    _box(monkeypatch, box)
    real_get = box.get
    calls = {"n": 0}

    def get(url, timeout=8.0):
        if url.endswith("/volume") and calls["n"] == 0:
            calls["n"] += 1
            raise O.SpeakerError("busy")
        return real_get(url, timeout)
    monkeypatch.setattr(O, "http_get", get)
    assert O.main(["--ip", "192.0.2.31", "reboot", "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["volume_before"] is None


def test_the_verdict_envelope_keeps_its_key_order():
    """The CLI contract: urls, cloud_leftovers, still_injected, missing, ok - in that order."""
    assert list(O.migration_verdict(_urls()).to_json()) == [
        "urls", "cloud_leftovers", "still_injected", "missing", "ok"]


def test_now_playing_defaults_when_the_speaker_leaves_fields_out():
    reading = O.parse_now_playing("<nowPlaying></nowPlaying>")
    assert (reading.source, reading.play_status, reading.item_name) == ("", "-", "-")
    assert not reading.is_standby and not reading.is_playing
