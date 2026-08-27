"""Tests for the migration verdict and the reboot proof."""
import soundtouch_onboard as O

LOCAL = {"margeServerUrl": "http://192.0.2.10:8000",
         "statsServerUrl": "http://192.0.2.10:8000",
         "swUpdateUrl": "http://192.0.2.10:8000/updates/soundtouch",
         "bmxRegistryUrl": "http://192.0.2.10:8000/bmx/registry/v1/services"}


def test_a_fully_local_speaker_passes():
    assert O.migration_verdict(dict(LOCAL))["ok"] is True


def test_one_cloud_url_fails_even_though_the_others_are_local():
    """This is the case that looks migrated and plays nothing."""
    urls = dict(LOCAL, bmxRegistryUrl="https://content.api.bose.io/bmx/registry/v1/services")
    verdict = O.migration_verdict(urls)
    assert verdict["ok"] is False
    assert "bmxRegistryUrl" in verdict["cloud_leftovers"]


def test_leftover_injection_fails_even_when_no_cloud_url_remains():
    urls = dict(LOCAL, margeServerUrl="http://192.0.2.10:8000;touch /tmp/remote_services")
    verdict = O.migration_verdict(urls)
    assert verdict["ok"] is False
    assert "margeServerUrl" in verdict["still_injected"]


def test_a_missing_field_fails():
    urls = dict(LOCAL)
    del urls["statsServerUrl"]
    verdict = O.migration_verdict(urls)
    assert verdict["ok"] is False
    assert verdict["missing"] == ["statsServerUrl"]


def test_an_empty_read_is_not_a_pass():
    """Reading nothing back must never look like a clean speaker."""
    assert O.migration_verdict({})["ok"] is False


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
