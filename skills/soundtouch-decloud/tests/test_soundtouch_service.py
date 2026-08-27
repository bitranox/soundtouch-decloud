"""Tests for the Docker preflight and the compose file it renders.

The compose rules are the ones that silently produce a service which starts, answers HTTP and
discovers nothing, so they are asserted rather than trusted.
"""
import pytest
import soundtouch_service as S


def test_render_uses_host_networking():
    """Discovery is SSDP and mDNS multicast, which Docker's bridge does not forward."""
    assert "network_mode: host" in S.render_compose("192.0.2.10")


def test_render_never_emits_a_ports_block():
    """A ports block is invalid with host networking and Docker only warns, so it reads as applying."""
    assert "ports:" not in S.render_compose("192.0.2.10")


def test_render_advertises_the_given_host_not_loopback():
    out = S.render_compose("192.0.2.10")
    assert "SERVER_URL: http://192.0.2.10:8000" in out
    assert "127.0.0.1" not in out and "localhost" not in out


def test_render_pins_the_requested_version():
    assert "bose-soundtouch:1.2.3" in S.render_compose("192.0.2.10", version="1.2.3")


@pytest.mark.parametrize("bad", ["127.0.0.1", "localhost", "::1", "0.0.0.0", ""])
def test_validate_host_rejects_addresses_a_speaker_cannot_call_back_to(bad):
    ok, _ = S.validate_host(bad)
    assert ok is False


@pytest.mark.parametrize("good", ["192.0.2.10", "198.51.100.4", "nas.example.com"])
def test_validate_host_accepts_a_real_address(good):
    ok, _ = S.validate_host(good)
    assert ok is True


def test_render_refuses_a_loopback_host():
    with pytest.raises(ValueError):
        S.render_compose("127.0.0.1")


def test_install_hint_is_specific_per_platform():
    assert "Docker Desktop" in S.install_hint("windows")
    assert "get.docker.com" in S.install_hint("debian")
    assert "Container Manager" in S.install_hint("nas")


def test_install_hint_for_an_unknown_platform_asks_which_one():
    hint = S.install_hint("plan9")
    assert "Ask which system" in hint and "debian" in hint


def test_install_hint_is_case_insensitive():
    assert S.install_hint("Windows") == S.install_hint("windows")


def test_docker_report_reports_absence_without_raising(monkeypatch):
    """A machine with no Docker is the normal case this walks the owner through, not an error."""
    monkeypatch.setattr(S.shutil, "which", lambda _: None)
    rep = S.docker_report()
    assert rep["docker"] is False and rep["compose"] is False
