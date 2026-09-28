"""Tests for how a speaker's state is turned into one verdict and one sentence for the owner."""
import json

import soundtouch_find as F

READY_SOURCES = {"TUNEIN": "READY", "LOCAL_INTERNET_RADIO": "READY", "RADIO_BROWSER": "READY"}


def _state(**over):
    base = {"ports": {"22": False, "17000": True, "8090": True}, "cloud_leftovers": {},
            "account": "1234567", "sources": dict(READY_SOURCES), "preset_count": 6}
    base.update(over)
    return base


def test_a_speaker_that_does_not_answer_is_not_answering():
    assert F.classify(_state(ports={"8090": False, "17000": False, "22": False})) == "not-answering"


def test_cloud_urls_mean_it_needs_migration():
    assert F.classify(_state(cloud_leftovers={"bmxRegistryUrl": "https://x.bose.io/y"})) == "needs-migration"


def test_no_account_is_reported_before_blaming_the_sources():
    """Without an account the speaker never contacts the service at all, so it outranks sources."""
    assert F.classify(_state(account="", sources={"TUNEIN": "ABSENT"})) == "needs-account"


def test_unmounted_sources_are_their_own_verdict():
    assert F.classify(_state(sources={"TUNEIN": "READY", "LOCAL_INTERNET_RADIO": "ABSENT",
                                      "RADIO_BROWSER": "READY"})) == "sources-not-ready"


def test_no_presets_is_its_own_verdict():
    assert F.classify(_state(preset_count=0)) == "needs-presets"


def test_a_working_speaker_is_ready():
    assert F.classify(_state()) == "ready"


def test_every_verdict_has_owner_facing_advice():
    for verdict in ("not-answering", "needs-migration", "registry-foreign", "needs-account",
                    "sources-not-ready", "needs-presets", "ready"):
        text = F.describe_state(verdict)
        assert text != verdict and len(text) > 20


def test_the_not_answering_advice_tells_the_owner_to_wake_it():
    """The commonest cause is a speaker idling, and pressing a button is the cheapest fix."""
    assert "press a button" in F.describe_state("not-answering").lower()


def test_the_sources_advice_gives_a_real_wait():
    assert "80" in F.describe_state("sources-not-ready")


def test_a_clock_left_in_2015_is_named_rather_than_called_ready():
    """Everything else green and no sound from any https station: the clock is the fault."""
    assert F.classify(_state(clock={"verdict": "wrong", "reading": "2015-07-06 20:36:50"})) \
        == "clock-wrong"


def test_a_clock_that_could_not_be_read_changes_no_verdict():
    """A speaker that answered nothing about its clock must not be reported as broken."""
    assert F.classify(_state(clock={"verdict": "unknown", "reading": None})) == "ready"


def test_a_structural_fault_outranks_a_wrong_clock():
    """Rewriting the service URLs comes first; the clock cannot be judged from a migrated box."""
    assert F.classify(_state(cloud_leftovers={"bmxRegistryUrl": "https://x.bose.io/y"},
                             clock={"verdict": "wrong", "reading": "2015-07-06 20:36:50"})) \
        == "needs-migration"


def test_the_clock_advice_names_the_symptom_and_the_repair():
    text = F.describe_state("clock-wrong").lower()
    assert "https" in text or "internet radio" in text
    assert text != "clock-wrong" and len(text) > 20


def test_a_registry_sending_the_speaker_elsewhere_is_named():
    """Every URL reads migrated, sources read READY, and radio is broken: only this says why."""
    assert F.classify(_state(registry={"verdict": "foreign"})) == "registry-foreign"


def test_the_registry_fault_ranks_below_migration_and_above_everything_else():
    assert F.classify(_state(registry={"verdict": "foreign"},
                             cloud_leftovers={"bmxRegistryUrl": "https://x.bose.io/y"})) == "needs-migration"
    assert F.classify(_state(registry={"verdict": "foreign"}, account="")) == "registry-foreign"


def test_an_unreadable_or_dns_mode_registry_leaves_the_verdict_alone():
    """Not knowing is not a fault, and DNS mode names the Bose cloud on purpose."""
    assert F.classify(_state(registry={"verdict": "unreadable"})) == "ready"
    assert F.classify(_state(registry={"verdict": "dns-mode"})) == "ready"


def test_the_registry_advice_names_the_file_that_causes_it():
    assert "settings.json" in F.describe_state("registry-foreign")


def test_the_registry_advice_says_the_speakers_must_restart():
    """A fixed registry is invisible to a speaker until it reboots; every check reads ok before."""
    assert "restart every speaker" in F.describe_state("registry-foreign")


GETPDO_OURS = """CurrentSystemConfiguration {
  bmxRegistryUrl {
    text: "http://192.0.2.10:8000/bmx/registry/v1/services"
  }
}"""


def _registry_body(host: str) -> str:
    return json.dumps({"bmx_services": [
        {"id": {"name": "TUNEIN"}, "baseUrl": f"{host}/bmx/tunein"},
        {"id": {"name": "LOCAL_INTERNET_RADIO"},
         "baseUrl": f"{host}/core02/svc-bmx-adapter-orion/prod/orion"}]})


def _fake_speaker(monkeypatch, registry_host: str) -> list[str]:
    fetched: list[str] = []

    def get(url, timeout=8.0):
        fetched.append(url)
        if url.endswith("/bmx/registry/v1/services"):
            return _registry_body(registry_host)
        if url.endswith("/info"):
            return "<info deviceID=\"AABBCC0000A1\"><name>Room1</name><margeAccountUUID>1</margeAccountUUID></info>"
        if url.endswith("/sources"):
            return "".join(f'<sourceItem source="{s}" status="READY" />' for s in READY_SOURCES)
        return "<presets>" + '<preset id="1"><ContentItem location="/station?data=x" /></preset>' * 6 + "</presets>"

    monkeypatch.setattr(F, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(F, "http_get", get)
    monkeypatch.setattr(F, "telnet_run", lambda _ip, cmds: [{"cmd": cmds[0], "reply": GETPDO_OURS}])
    monkeypatch.setattr(F, "http_date_header", lambda _ip: None)
    return fetched


def test_speaker_state_reads_the_registry_the_speaker_itself_uses(monkeypatch):
    fetched = _fake_speaker(monkeypatch, "http://192.0.2.99:8000")
    state = F.speaker_state("192.0.2.31")
    assert "http://192.0.2.10:8000/bmx/registry/v1/services" in fetched
    assert state["verdict"] == "registry-foreign"
    assert state["registry"]["advertised"]["TUNEIN"].startswith("http://192.0.2.99:8000")


def test_speaker_state_with_its_own_registry_is_ready(monkeypatch):
    _fake_speaker(monkeypatch, "http://192.0.2.10:8000")
    assert F.speaker_state("192.0.2.31")["verdict"] == "ready"
