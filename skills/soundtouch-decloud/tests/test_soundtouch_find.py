"""Tests for how a speaker's state is turned into one verdict and one sentence for the owner."""
import json

import soundtouch_find as F
from soundtouch_core import (
    API_PORT,
    SSH_PORT,
    TELNET_PORT,
    ClockState,
    ClockVerdict,
    RadioSource,
    RadioSources,
    RegistryCheck,
    RegistryVerdict,
    ServiceUrls,
    TelnetReply,
    UrlField,
)

READY_SOURCES = {"TUNEIN": "READY", "LOCAL_INTERNET_RADIO": "READY", "RADIO_BROWSER": "READY"}
OPEN_PORTS = {SSH_PORT: False, TELNET_PORT: True, API_PORT: True}
CLOUD = {"bmxRegistryUrl": "https://x.bose.io/y"}


def _sources(statuses=None) -> RadioSources:
    return RadioSources({RadioSource(k): v for k, v in (statuses or READY_SOURCES).items()})


def _urls(values) -> ServiceUrls:
    return ServiceUrls({UrlField(k): v for k, v in values.items()})


def _state(*, ports=None, cloud_leftovers=None, account="1234567", sources=None, preset_count=6,
           registry=None, clock=None, registry_verdict_word=None, clock_verdict_word=None,
           clock_reading=None) -> F.SpeakerState:
    """A fully healthy answering speaker, with the named parts swapped."""
    if registry_verdict_word is not None:
        registry = RegistryCheck(verdict=RegistryVerdict(registry_verdict_word))
    if clock_verdict_word is not None:
        clock = ClockState(ClockVerdict(clock_verdict_word), clock_reading)
    return F.SpeakerState(
        ip="192.0.2.31", ports=OPEN_PORTS if ports is None else ports,
        info=F.DeviceInfo(name="Room1", device_id="AABBCC0000A1", account=account),
        urls=_urls(cloud_leftovers or {}), cloud_leftovers=_urls(cloud_leftovers or {}),
        registry=registry, sources=_sources(sources), preset_count=preset_count, clock=clock)


def test_a_speaker_that_does_not_answer_is_not_answering():
    assert F.classify(_state(ports={API_PORT: False, TELNET_PORT: False, SSH_PORT: False})) == F.SpeakerVerdict.NOT_ANSWERING


def test_cloud_urls_mean_it_needs_migration():
    assert F.classify(_state(cloud_leftovers=CLOUD)) == F.SpeakerVerdict.NEEDS_MIGRATION


def test_no_account_is_reported_before_blaming_the_sources():
    """Without an account the speaker never contacts the service at all, so it outranks sources."""
    assert F.classify(_state(account="", sources={"TUNEIN": "ABSENT"})) == F.SpeakerVerdict.NEEDS_ACCOUNT


def test_unmounted_sources_are_their_own_verdict():
    assert F.classify(_state(sources={"TUNEIN": "READY", "LOCAL_INTERNET_RADIO": "ABSENT",
                                      "RADIO_BROWSER": "READY"})) == F.SpeakerVerdict.SOURCES_NOT_READY


def test_no_presets_is_its_own_verdict():
    assert F.classify(_state(preset_count=0)) == F.SpeakerVerdict.NEEDS_PRESETS


def test_a_working_speaker_is_ready():
    assert F.classify(_state()) == F.SpeakerVerdict.READY


def test_every_verdict_has_owner_facing_advice():
    for verdict in F.SpeakerVerdict:
        text = F.describe_state(verdict)
        assert text != verdict and len(text) > 20


def test_the_not_answering_advice_tells_the_owner_to_wake_it():
    """The commonest cause is a speaker idling, and pressing a button is the cheapest fix."""
    assert "press a button" in F.describe_state(F.SpeakerVerdict.NOT_ANSWERING).lower()


def test_the_sources_advice_gives_a_real_wait():
    assert "80" in F.describe_state(F.SpeakerVerdict.SOURCES_NOT_READY)


def test_a_clock_left_in_2015_is_named_rather_than_called_ready():
    """Everything else green and no sound from any https station: the clock is the fault."""
    assert F.classify(_state(clock_verdict_word="wrong", clock_reading="2015-07-06 20:36:50")) \
        == F.SpeakerVerdict.CLOCK_WRONG


def test_a_clock_that_could_not_be_read_changes_no_verdict():
    """A speaker that answered nothing about its clock must not be reported as broken."""
    assert F.classify(_state(clock_verdict_word="unknown")) == F.SpeakerVerdict.READY


def test_a_structural_fault_outranks_a_wrong_clock():
    """Rewriting the service URLs comes first; the clock cannot be judged from a migrated box."""
    assert F.classify(_state(cloud_leftovers=CLOUD,
                             clock_verdict_word="wrong", clock_reading="2015-07-06 20:36:50")) \
        == F.SpeakerVerdict.NEEDS_MIGRATION


def test_the_clock_advice_names_the_symptom_and_the_repair():
    text = F.describe_state(F.SpeakerVerdict.CLOCK_WRONG).lower()
    assert "https" in text or "internet radio" in text
    assert text != "clock-wrong" and len(text) > 20


def test_a_registry_sending_the_speaker_elsewhere_is_named():
    """Every URL reads migrated, sources read READY, and radio is broken: only this says why."""
    assert F.classify(_state(registry_verdict_word="foreign")) == F.SpeakerVerdict.REGISTRY_FOREIGN


def test_the_registry_fault_ranks_below_migration_and_above_everything_else():
    assert F.classify(_state(registry_verdict_word="foreign",
                             cloud_leftovers=CLOUD)) == F.SpeakerVerdict.NEEDS_MIGRATION
    assert F.classify(_state(registry_verdict_word="foreign", account="")) == F.SpeakerVerdict.REGISTRY_FOREIGN


def test_an_unreadable_or_dns_mode_registry_leaves_the_verdict_alone():
    """Not knowing is not a fault, and DNS mode names the Bose cloud on purpose."""
    assert F.classify(_state(registry_verdict_word="unreadable")) == F.SpeakerVerdict.READY
    assert F.classify(_state(registry_verdict_word="dns-mode")) == F.SpeakerVerdict.READY


def test_the_registry_advice_names_the_file_that_causes_it():
    assert "settings.json" in F.describe_state(F.SpeakerVerdict.REGISTRY_FOREIGN)


def test_the_registry_advice_says_the_speakers_must_restart():
    """A fixed registry is invisible to a speaker until it reboots; every check reads ok before."""
    assert "restart every speaker" in F.describe_state(F.SpeakerVerdict.REGISTRY_FOREIGN)


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
    monkeypatch.setattr(F, "telnet_run", lambda _ip, cmds: [TelnetReply(cmd=cmds[0], reply=GETPDO_OURS, complete=True)])
    monkeypatch.setattr(F, "http_date_header", lambda _ip: None)
    return fetched


def test_speaker_state_reads_the_registry_the_speaker_itself_uses(monkeypatch):
    fetched = _fake_speaker(monkeypatch, "http://192.0.2.99:8000")
    state = F.speaker_state("192.0.2.31")
    assert "http://192.0.2.10:8000/bmx/registry/v1/services" in fetched
    assert state.verdict == F.SpeakerVerdict.REGISTRY_FOREIGN
    assert state.registry.advertised["TUNEIN"].startswith("http://192.0.2.99:8000")


def test_speaker_state_with_its_own_registry_is_ready(monkeypatch):
    _fake_speaker(monkeypatch, "http://192.0.2.10:8000")
    assert F.speaker_state("192.0.2.31").verdict == F.SpeakerVerdict.READY


def test_a_speaker_without_radio_browser_is_ready():
    """The Wireless Link Adapter never lists RADIO_BROWSER; an unlisted source is not a loading one."""
    assert F.classify(_state(sources={"TUNEIN": "READY", "LOCAL_INTERNET_RADIO": "READY",
                                      "RADIO_BROWSER": "ABSENT"})) == F.SpeakerVerdict.READY


def test_an_unreadable_info_is_not_blamed_on_the_account():
    """The account was never read, so 'no account attached' would be a claim nothing supports."""
    state = F.SpeakerState(ip="192.0.2.31", ports=OPEN_PORTS, info_error="timed out",
                           sources=_sources(), preset_count=6)
    assert F.classify(state) == F.SpeakerVerdict.INFO_UNREADABLE
    assert json.loads(json.dumps(state.to_json()))["info_error"] == "timed out"


def test_a_migration_fault_still_outranks_an_unreadable_info():
    state = F.SpeakerState(ip="192.0.2.31", ports=OPEN_PORTS, info_error="timed out",
                           urls=_urls(CLOUD), cloud_leftovers=_urls(CLOUD))
    assert F.classify(state) == F.SpeakerVerdict.NEEDS_MIGRATION
