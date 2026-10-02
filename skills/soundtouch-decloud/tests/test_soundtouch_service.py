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


# Every variable upstream's soundtouch-service reads from the environment, checked at v0.138.0
# (cmd/soundtouch-service/main.go). A name missing here that render emits is one upstream ignores.
UPSTREAM_ENV = {
    "AMAZON_CLIENT_ID", "AMAZON_CLIENT_SECRET", "AMAZON_PROFILE_URL", "AMAZON_REDIRECT_URI",
    "AMAZON_TOKEN_URL", "BASE_URL", "BIND_ADDR", "DATA_DIR", "DEPLOYMENT_MODE",
    "DEVICE_SEED_RETRY_INTERVAL", "DEVICE_SEED_RETRY_WINDOW", "DISCOVERY_ENABLED",
    "DISCOVERY_INTERVAL", "DNS_BIND_ADDR", "DNS_UPSTREAM", "ENABLE_DNS_DISCOVERY", "HTTPS_PORT",
    "HTTPS_SERVER_URL", "INTERNAL_PATHS", "LOG_PROXY_BODY", "MGMT_PASSWORD", "MGMT_USERNAME",
    "MIGRATION_DRY_RUN", "MIGRATION_ENABLED", "PORT", "RECORD_INTERACTIONS", "REDACT_PROXY_LOGS",
    "SERVER_URL", "SPOTIFY_API_BASE", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI", "SPOTIFY_TOKEN_URL", "STOCKHOLM_BASE_PATH", "STOCKHOLM_DIR",
    "TLS_EXTRA_HOST", "TTS_APP_KEY", "TTS_GOOGLE_API_KEY", "TTS_GOOGLE_ENDPOINT", "TTS_LANGUAGE",
    "TTS_PROVIDER", "TTS_VOICE", "TTS_VOLUME", "TUNEIN_API_URL", "TUNEIN_OPML_URL",
    "UPDATE_CHECK_ENABLED", "UPDATE_CHECK_INTERVAL",
}


def _env_keys(compose: str) -> set[str]:
    block = compose.split("    environment:\n", 1)[1].split("    volumes:", 1)[0]
    return {line.strip().split(":", 1)[0] for line in block.splitlines() if line.strip()}


def test_render_emits_exactly_the_variables_it_means_to():
    assert _env_keys(S.render_compose("192.0.2.10")) == {
        "PORT", "HTTPS_PORT", "DATA_DIR", "SERVER_URL", "MGMT_USERNAME", "MGMT_PASSWORD",
        "RECORD_INTERACTIONS", "DISCOVERY_INTERVAL"}


def test_every_rendered_variable_is_one_upstream_reads():
    assert _env_keys(S.render_compose("192.0.2.10")) <= UPSTREAM_ENV


def test_render_leaves_the_https_url_to_be_derived():
    """Upstream derives it from SERVER_URL when unset. Setting it pins a second copy of the
    address that goes stale the day SERVER_URL is changed without it."""
    assert "HTTPS_SERVER_URL" not in S.render_compose("192.0.2.10")


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
    assert rep.docker is False and rep.compose is False
    assert rep.to_json() == {"docker": False, "compose": False}


def test_docker_report_serialises_only_the_keys_its_path_filled():
    assert S.DockerReport(docker=True, compose=True, compose_version="v2").to_json() == {
        "docker": True, "compose": True, "compose_version": "v2"}
    assert S.DockerReport(docker=True, compose=False, compose_error="boom").to_json() == {
        "docker": True, "compose": False, "compose_error": "boom"}


def test_render_in_ports_mode_publishes_ports_and_drops_host_networking():
    out = S.render_compose("192.0.2.10", network=S.Network.PORTS)
    assert '"8000:8000"' in out and "network_mode" not in out


def test_render_refuses_an_unknown_network_by_name():
    with pytest.raises(ValueError, match="network must be 'host' or 'ports', got 'bridge'"):
        S.render_compose("192.0.2.10", network="bridge")  # type: ignore[arg-type]


def test_the_device_listing_counts_every_entry_but_names_only_objects():
    listing = S.parse_devices([{"name": "Kitchen"}, 7, {"id": 1}])
    assert listing == S.DeviceListing(count=3, names=("Kitchen", None))
    assert listing is not None
    assert listing.to_json() == {"devices": 3, "names": ["Kitchen", None]}


def test_a_payload_that_is_not_a_list_is_not_a_device_listing():
    assert S.parse_devices({"name": "x"}) is None
