"""Tests for recovering a station's stream URL and proving it still plays.

The XML fixtures keep the exact shape a speaker and the service really produce, including the
`data=` blob a pre-shutdown preset carries, because the whole point of harvest is that it reads
those bytes rather than a tidied version of them.
"""
import base64
import json
import urllib.parse

import pytest
import soundtouch_core as C
import soundtouch_presets as P


def _cloud(name: str, stream: str) -> str:
    """A Bose Orion station location of the shape a pre-shutdown preset stores."""
    blob = base64.b64encode(json.dumps({"name": name, "imageUrl": "", "streamUrl": stream}).encode())
    return ("https://content.api.bose.io/core02/svc-bmx-adapter-orion/prod/orion/station?data="
            + urllib.parse.quote(blob.decode(), safe=""))


PREMIGRATION = (
    '<?xml version="1.0" encoding="UTF-8" ?><presets>'
    f'<preset id="1" createdOn="1545604828"><ContentItem source="LOCAL_INTERNET_RADIO" '
    f'type="stationurl" location="{_cloud("Example Radio", "https://radio.example.com/live")}" '
    'sourceAccount="" isPresetable="true"><itemName>Example Radio</itemName>'
    '<containerArt /></ContentItem></preset>'
    '<preset id="2"><ContentItem source="TUNEIN" type="stationurl" '
    'location="https://content.api.bose.io/core02/svc-bmx-adapter-orion/prod/orion/station'
    '?data=eyJuYW1lIjoiQ2F0YWxvZ3VlIn0%3D" sourceAccount="7654321" isPresetable="true">'
    '<itemName>Catalogue Station</itemName></ContentItem></preset>'
    '</presets>')


# --- recovering the stream URL --------------------------------------------------------------

def test_a_pre_shutdown_preset_yields_the_stream_it_carries():
    assert C.decode_cloud_location(_cloud("X", "http://a.example/b")) == "http://a.example/b"


def test_a_catalogue_station_carries_no_stream_to_recover():
    location = ("https://content.api.bose.io/x/station?data="
                + base64.b64encode(json.dumps({"name": "Catalogue"}).encode()).decode())
    assert C.decode_cloud_location(location) == ""


@pytest.mark.parametrize("location", [
    "https://content.api.bose.io/x/station?data=!!!not-base64!!!",
    "https://content.api.bose.io/x/station",
    "s24885",
    "",
])
def test_a_location_with_nothing_to_decode_yields_empty(location):
    assert C.decode_cloud_location(location) == ""


def test_our_own_playback_wrapping_round_trips():
    wrapped = C.playback_location("http://192.0.2.10:8000", "https://radio.example.com/s", "S")
    assert C.stream_url_from_location(wrapped) == "https://radio.example.com/s"


def test_a_plain_stream_url_is_already_the_answer():
    assert C.stream_url_from_location("https://radio.example.com/s") == "https://radio.example.com/s"


def test_a_surviving_cloud_url_is_never_passed_through_as_a_stream():
    """The fallback must not hand back a bose.io URL just because it parses as a URL."""
    assert C.stream_url_from_location("https://content.api.bose.io/x/station") == ""


def test_is_cloud_location_knows_the_dead_hosts():
    assert C.is_cloud_location("https://content.api.bose.io/x")
    assert not C.is_cloud_location("https://radio.example.com/x")


# --- harvest ---------------------------------------------------------------------------------

def test_harvest_recovers_the_stream_and_keeps_button_and_name():
    entries = C.harvest_presets(PREMIGRATION)
    assert entries[0] == {"buttonNumber": 1, "name": "Example Radio",
                          "location": "https://radio.example.com/live",
                          "contentItemType": "stationurl", "source": "LOCAL_INTERNET_RADIO"}


def test_harvest_leaves_a_hole_rather_than_dropping_a_station_it_cannot_resolve():
    entries = C.harvest_presets(PREMIGRATION)
    assert entries[1]["location"] == ""
    assert entries[1]["name"] == "Catalogue Station"


def test_preset_name_reads_the_button_it_was_asked_for():
    assert C.preset_name(PREMIGRATION, 2) == "Catalogue Station"
    assert C.preset_name(PREMIGRATION, 5) == ""


# --- what came back --------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "ctype", "head", "expected"), [
    (200, "audio/mpeg", "ID3", "audio"),
    (200, "audio/aac", "\xff\xf1", "audio"),
    (200, "application/ogg", "OggS", "audio"),
    (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/b", "playlist"),
    (200, "audio/x-scpls", "[playlist]\nFile1=http://a.example/b", "playlist"),
    (200, "text/plain", "#EXTM3U\nhttp://a.example/b", "playlist"),
    (200, "application/x-mpegurl", "#EXTM3U\n#EXT-X-VERSION:3\n", "hls"),
    (200, "text/html", "<html><body>Listen live", "not-audio"),
    (404, "", "", "dead"),
    (503, "", "", "dead"),
    (None, "", "", "dead"),
])
def test_classify_stream_separates_audio_from_things_that_merely_look_like_it(status, ctype, head,
                                                                             expected):
    assert C.classify_stream(status, ctype, head) == expected


def test_a_playlist_served_as_audio_is_not_reported_as_audio():
    """The trap this exists for: `audio/x-mpegurl` passes a bare `audio/` test and plays nothing."""
    assert C.classify_stream(200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/b") != "audio"


def test_playlist_targets_reads_both_playlist_dialects():
    assert C.playlist_targets("#EXTM3U\n#comment\n\nhttp://a.example/b\n") == ["http://a.example/b"]
    assert C.playlist_targets("[playlist]\nFile1=http://a.example/b\n") == ["http://a.example/b"]


def test_playlist_targets_keeps_order_and_drops_duplicates():
    body = "http://a.example/b\nhttp://c.example/d\nhttp://a.example/b\n"
    assert C.playlist_targets(body) == ["http://a.example/b", "http://c.example/d"]


# --- the verdict, with the network injected ----------------------------------------------------

def _canned(responses):
    """A fetch seam returning a scripted answer per URL, so no test touches the network."""
    def fetch(url, timeout=8.0):
        return responses[url]
    return fetch


def test_a_live_stream_is_playable():
    fetch = _canned({"http://a.example/s": (200, "audio/mpeg", "ID3")})
    assert P.stream_verdict("http://a.example/s", fetch=fetch)["playable"] is True


def test_a_dead_stream_is_reported_with_its_status():
    fetch = _canned({"http://a.example/s": (404, "", "")})
    verdict = P.stream_verdict("http://a.example/s", fetch=fetch)
    assert (verdict["playable"], verdict["verdict"], verdict["status"]) == (False, "dead", 404)


def test_a_playlist_is_followed_one_level_to_the_real_stream():
    fetch = _canned({
        "http://a.example/list.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/s"),
        "http://a.example/s": (200, "audio/mpeg", "ID3"),
    })
    verdict = P.stream_verdict("http://a.example/list.m3u", fetch=fetch)
    assert verdict["playable"] is True
    assert verdict["url"] == "http://a.example/list.m3u"
    assert verdict["resolved_to"] == "http://a.example/s"


def test_a_playlist_of_playlists_is_not_followed_forever():
    fetch = _canned({
        "http://a.example/1.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/2.m3u"),
        "http://a.example/2.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/s"),
    })
    verdict = P.stream_verdict("http://a.example/1.m3u", fetch=fetch)
    assert verdict["playable"] is False
    assert verdict["verdict"] == "playlist"


def test_an_empty_playlist_is_reported_rather_than_followed():
    fetch = _canned({"http://a.example/l.m3u": (200, "audio/x-mpegurl", "#EXTM3U\n")})
    assert P.stream_verdict("http://a.example/l.m3u", fetch=fetch)["playable"] is False


def test_a_landing_page_is_not_mistaken_for_a_station():
    fetch = _canned({"http://a.example/s": (200, "text/html", "<html>Listen live</html>")})
    verdict = P.stream_verdict("http://a.example/s", fetch=fetch)
    assert (verdict["playable"], verdict["verdict"]) == (False, "not-audio")


# --- a template with holes cannot be written to a speaker ---------------------------------------

def _write(tmp_path, data):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _template(location):
    return {"deviceId": "00005E005300", "name": "Example Speaker",
            "presets": [{"buttonNumber": 1, "name": "Example Radio", "location": location,
                         "contentItemType": "stationurl", "source": "LOCAL_INTERNET_RADIO"}]}


def test_an_unresolved_hole_is_refused_by_the_writing_path(tmp_path):
    with pytest.raises(ValueError, match="no stream URL yet"):
        P.load_template(_write(tmp_path, _template("")))


def test_a_station_id_left_in_place_is_refused_too(tmp_path):
    with pytest.raises(ValueError, match="no stream URL yet"):
        P.load_template(_write(tmp_path, _template("s24885")))


def test_the_lenient_reader_accepts_the_file_harvest_just_wrote(tmp_path):
    """validate has to be able to read the very holes it exists to report."""
    assert len(P.load_partial_template(_write(tmp_path, _template("")))["presets"]) == 1


# --- the CLI ------------------------------------------------------------------------------------

def test_harvest_writes_a_template_and_reports_what_still_needs_research(tmp_path, capsys):
    src, out = tmp_path / "b.xml", tmp_path / "t.json"
    src.write_text(PREMIGRATION, encoding="utf-8")
    rc = P.main(["harvest", "--backup", str(src), "--out", str(out), "--name", "Example Speaker"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["data"]["unresolved"] == 1
    assert body["data"]["needs_research"] == ["Catalogue Station"]
    assert len(json.loads(out.read_text(encoding="utf-8"))["presets"]) == 2


def test_harvest_of_a_fully_resolvable_backup_exits_zero(tmp_path, capsys):
    src = tmp_path / "b.xml"
    src.write_text('<presets><preset id="1"><ContentItem '
                   f'location="{_cloud("Example Radio", "https://radio.example.com/live")}">'
                   "<itemName>Example Radio</itemName></ContentItem></preset></presets>",
                   encoding="utf-8")
    assert P.main(["harvest", "--backup", str(src), "--out", str(tmp_path / "t.json")]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["unresolved"] == 0


def test_validate_reports_a_broken_template_as_an_error_not_a_verdict(tmp_path, capsys):
    bad = tmp_path / "t.json"
    bad.write_text("{not json", encoding="utf-8")
    assert P.main(["validate", "--template", str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def _explodes(url, timeout=8.0):
    raise AssertionError("an unresearched hole must not be fetched")


def test_an_unresearched_hole_is_missing_not_dead():
    """`dead` and `missing` call for opposite actions, and fetching "" cannot tell them apart."""
    verdict = P.stream_verdict("", fetch=_explodes)
    assert verdict["verdict"] == "missing"
    assert verdict["playable"] is False


def test_validate_reports_a_hole_without_calling_it_dead(tmp_path, capsys):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"presets": [
        {"buttonNumber": 1, "name": "Needs Research", "location": ""}]}), encoding="utf-8")
    assert P.main(["validate", "--template", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)["data"]["results"][0]
    assert (result["verdict"], result["name"]) == ("missing", "Needs Research")
