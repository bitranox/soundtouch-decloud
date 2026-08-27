"""Tests for how a speaker's state is turned into one verdict and one sentence for the owner."""
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
    for verdict in ("not-answering", "needs-migration", "needs-account", "sources-not-ready",
                    "needs-presets", "ready"):
        text = F.describe_state(verdict)
        assert text != verdict and len(text) > 20


def test_the_not_answering_advice_tells_the_owner_to_wake_it():
    """The commonest cause is a speaker idling, and pressing a button is the cheapest fix."""
    assert "press a button" in F.describe_state("not-answering").lower()


def test_the_sources_advice_gives_a_real_wait():
    assert "80" in F.describe_state("sources-not-ready")
