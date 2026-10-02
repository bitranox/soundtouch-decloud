"""Tests for recovering a station's stream URL and proving it still plays.

The XML fixtures keep the exact shape a speaker and the service really produce, including the
`data=` blob a pre-shutdown preset carries, because the whole point of harvest is that it reads
those bytes rather than a tidied version of them.
"""
import base64
import json
import pathlib
import urllib.parse
from collections.abc import Callable

import pytest
import soundtouch_core as C
import soundtouch_presets as P
from soundtouch_core import PresetEntry, StreamKind


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
    '<preset id="3"><ContentItem source="SPOTIFY" type="tracklisturl" '
    'location="/playback/container/c3BvdGlmeTphbGJ1bTox" sourceAccount="owner@example.com" '
    'isPresetable="true"><itemName>An Album</itemName></ContentItem></preset>'
    '<preset id="4"><ContentItem source="STORED_MUSIC" type="album" '
    'location="1$4$5" sourceAccount="00000000-0000-0000-0000-000000000000/0" '
    'isPresetable="true"><itemName>Library Album</itemName></ContentItem></preset>'
    '</presets>')


# --- recovering the stream URL --------------------------------------------------------------

def test_a_pre_shutdown_preset_yields_the_stream_it_carries() -> None:
    assert C.decode_cloud_location(_cloud("X", "http://a.example/b")) == "http://a.example/b"


def test_a_catalogue_station_carries_no_stream_to_recover() -> None:
    location = ("https://content.api.bose.io/x/station?data="
                + base64.b64encode(json.dumps({"name": "Catalogue"}).encode()).decode())
    assert C.decode_cloud_location(location) == ""


@pytest.mark.parametrize("location", [
    "https://content.api.bose.io/x/station?data=!!!not-base64!!!",
    "https://content.api.bose.io/x/station",
    "s24885",
    "",
])
def test_a_location_with_nothing_to_decode_yields_empty(location: str) -> None:
    assert C.decode_cloud_location(location) == ""


def test_our_own_orion_wrapping_round_trips() -> None:
    wrapped = C.orion_location("https://radio.example.com/s", "S")
    assert C.stream_url_from_location(wrapped) == "https://radio.example.com/s"


def test_a_plain_stream_url_is_already_the_answer() -> None:
    assert C.stream_url_from_location("https://radio.example.com/s") == "https://radio.example.com/s"


def test_a_surviving_cloud_url_is_never_passed_through_as_a_stream() -> None:
    """The fallback must not hand back a bose.io URL just because it parses as a URL."""
    assert C.stream_url_from_location("https://content.api.bose.io/x/station") == ""


def test_is_cloud_location_knows_the_dead_hosts() -> None:
    assert C.is_cloud_location("https://content.api.bose.io/x")
    assert not C.is_cloud_location("https://radio.example.com/x")


# --- harvest ---------------------------------------------------------------------------------

def test_harvest_recovers_the_stream_and_keeps_button_and_name() -> None:
    entries = C.harvest_presets(C.parse_presets(PREMIGRATION))
    assert entries[0].to_json() == {"buttonNumber": 1, "name": "Example Radio",
                          "location": "https://radio.example.com/live",
                          "contentItemType": "stationurl", "source": "LOCAL_INTERNET_RADIO"}


def test_harvest_leaves_a_hole_rather_than_dropping_a_station_it_cannot_resolve() -> None:
    entries = C.harvest_presets(C.parse_presets(PREMIGRATION))
    assert entries[1].location == ""
    assert entries[1].name == "Catalogue Station"


@pytest.mark.parametrize(("button", "source", "ctype", "location"), [
    (3, "SPOTIFY", "tracklisturl", "/playback/container/c3BvdGlmeTphbGJ1bTox"),
    (4, "STORED_MUSIC", "album", "1$4$5"),
])
def test_harvest_keeps_a_non_radio_preset_exactly_as_it_was(button: int, source: str, ctype: str,
                                                            location: str) -> None:
    """Flattening these to LOCAL_INTERNET_RADIO turned an album into a named radio hole."""
    entry = next(e for e in C.harvest_presets(C.parse_presets(PREMIGRATION)) if e.button_number == button)
    assert entry.keep is True
    assert (entry.source, entry.content_item_type, entry.location) == (source, ctype, location)


def test_harvest_does_not_mark_a_radio_preset_as_kept() -> None:
    """A TUNEIN hole is radio to research, not something to leave alone."""
    entries = C.harvest_presets(C.parse_presets(PREMIGRATION))
    assert "keep" not in entries[0].to_json() and "keep" not in entries[1].to_json()


def test_harvest_reports_kept_presets_as_resolved_not_as_holes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    backup = tmp_path / "presets.xml"
    backup.write_text(PREMIGRATION, encoding="utf-8")
    P.main(["harvest", "--backup", str(backup), "--out", str(tmp_path / "t.json")])
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["needs_research"] == ["Catalogue Station"]
    assert data["kept"] == ["An Album", "Library Album"]


def test_harvest_unescapes_what_the_speaker_escaped() -> None:
    """The speaker returns XML: a name or a bare URL holding & arrives as &amp;. Kept literally,
    `restore` escapes it again and the button is named "Rock &amp; Roll" for good."""
    raw = ('<presets><preset id="1"><ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
           'location="https://radio.example.com/s?a=1&amp;b=2" sourceAccount="">'
           "<itemName>Rock &amp; Roll &lt;FM&gt;</itemName></ContentItem></preset></presets>")
    entry = C.harvest_presets(C.parse_presets(raw))[0]
    assert (entry.name, entry.location) == ("Rock & Roll <FM>", "https://radio.example.com/s?a=1&b=2")


def test_a_bare_url_with_an_escaped_ampersand_still_compares_equal() -> None:
    raw = ('<presets><preset id="1"><ContentItem source="LOCAL_INTERNET_RADIO" '
           'location="https://radio.example.com/s?a=1&amp;b=2" /></preset></presets>')
    wanted = [PresetEntry(button_number=1, name="A", location="https://radio.example.com/s?a=1&b=2")]
    assert C.slots_to_write(C.parse_presets(raw), wanted) == []


def test_parsing_names_each_button_from_its_own_slot() -> None:
    items = C.parse_presets(PREMIGRATION).items
    assert items[2].name == "Catalogue Station"
    assert 5 not in items


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
def test_classify_stream_separates_audio_from_things_that_merely_look_like_it(
        status: int | None, ctype: str, head: str, expected: str) -> None:
    assert C.classify_stream(status, ctype, head) == expected


def test_a_playlist_served_as_audio_is_not_reported_as_audio() -> None:
    """The trap this exists for: `audio/x-mpegurl` passes a bare `audio/` test and plays nothing."""
    assert C.classify_stream(200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/b") != "audio"


def test_playlist_targets_reads_both_playlist_dialects() -> None:
    assert C.playlist_targets("#EXTM3U\n#comment\n\nhttp://a.example/b\n") == ["http://a.example/b"]
    assert C.playlist_targets("[playlist]\nFile1=http://a.example/b\n") == ["http://a.example/b"]


def test_playlist_targets_keeps_order_and_drops_duplicates() -> None:
    body = "http://a.example/b\nhttp://c.example/d\nhttp://a.example/b\n"
    assert C.playlist_targets(body) == ["http://a.example/b", "http://c.example/d"]


# --- the verdict, with the network injected ----------------------------------------------------

def _canned(responses: dict[str, tuple[int | None, str, str]]) -> Callable[[str], tuple[int | None, str, str]]:
    """A fetch seam returning a scripted answer per URL, so no test touches the network."""
    def fetch(url: str, timeout: float = 8.0) -> tuple[int | None, str, str]:
        return responses[url]
    return fetch


def test_a_live_stream_is_playable() -> None:
    fetch = _canned({"http://a.example/s": (200, "audio/mpeg", "ID3")})
    assert P.stream_verdict("http://a.example/s", fetch=fetch).playable is True


def test_a_dead_stream_is_reported_with_its_status() -> None:
    fetch = _canned({"http://a.example/s": (404, "", "")})
    verdict = P.stream_verdict("http://a.example/s", fetch=fetch)
    assert (verdict.playable, verdict.verdict, verdict.status) == (False, StreamKind.DEAD, 404)


def test_a_playlist_is_followed_one_level_to_the_real_stream() -> None:
    fetch = _canned({
        "http://a.example/list.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/s"),
        "http://a.example/s": (200, "audio/mpeg", "ID3"),
    })
    verdict = P.stream_verdict("http://a.example/list.m3u", fetch=fetch)
    assert verdict.playable is True
    assert verdict.url == "http://a.example/list.m3u"
    assert verdict.resolved_to == "http://a.example/s"


def test_a_playlist_of_playlists_is_not_followed_forever() -> None:
    fetch = _canned({
        "http://a.example/1.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/2.m3u"),
        "http://a.example/2.m3u": (200, "audio/x-mpegurl", "#EXTM3U\nhttp://a.example/s"),
    })
    verdict = P.stream_verdict("http://a.example/1.m3u", fetch=fetch)
    assert verdict.playable is False
    assert verdict.verdict == StreamKind.PLAYLIST


def test_an_empty_playlist_is_reported_rather_than_followed() -> None:
    fetch = _canned({"http://a.example/l.m3u": (200, "audio/x-mpegurl", "#EXTM3U\n")})
    assert P.stream_verdict("http://a.example/l.m3u", fetch=fetch).playable is False


def test_a_landing_page_is_not_mistaken_for_a_station() -> None:
    fetch = _canned({"http://a.example/s": (200, "text/html", "<html>Listen live</html>")})
    verdict = P.stream_verdict("http://a.example/s", fetch=fetch)
    assert (verdict.playable, verdict.verdict) == (False, StreamKind.NOT_AUDIO)


# --- a template with holes cannot be written to a speaker ---------------------------------------

def _write(tmp_path: pathlib.Path, data: object) -> str:
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _template(location: str) -> dict[str, object]:
    return {"deviceId": "00005E005300", "name": "Example Speaker",
            "presets": [{"buttonNumber": 1, "name": "Example Radio", "location": location,
                         "contentItemType": "stationurl", "source": "LOCAL_INTERNET_RADIO"}]}


def test_an_unresolved_hole_is_refused_by_the_writing_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="no stream URL yet"):
        P.load_template(_write(tmp_path, _template("")))


def test_a_station_id_left_in_place_is_refused_too(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="no stream URL yet"):
        P.load_template(_write(tmp_path, _template("s24885")))


def test_the_lenient_reader_accepts_the_file_harvest_just_wrote(tmp_path: pathlib.Path) -> None:
    """validate has to be able to read the very holes it exists to report."""
    assert len(P.load_partial_template(_write(tmp_path, _template("")))) == 1


# --- the CLI ------------------------------------------------------------------------------------

def test_harvest_writes_a_template_and_reports_what_still_needs_research(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    src, out = tmp_path / "b.xml", tmp_path / "t.json"
    src.write_text(PREMIGRATION, encoding="utf-8")
    rc = P.main(["harvest", "--backup", str(src), "--out", str(out), "--name", "Example Speaker"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["data"]["unresolved"] == 1
    assert body["data"]["needs_research"] == ["Catalogue Station"]
    assert len(json.loads(out.read_text(encoding="utf-8"))["presets"]) == 4


def test_harvest_of_a_fully_resolvable_backup_exits_zero(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "b.xml"
    src.write_text('<presets><preset id="1"><ContentItem '
                   f'location="{_cloud("Example Radio", "https://radio.example.com/live")}">'
                   "<itemName>Example Radio</itemName></ContentItem></preset></presets>",
                   encoding="utf-8")
    assert P.main(["harvest", "--backup", str(src), "--out", str(tmp_path / "t.json")]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["unresolved"] == 0


def test_validate_reports_a_broken_template_as_an_error_not_a_verdict(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "t.json"
    bad.write_text("{not json", encoding="utf-8")
    assert P.main(["validate", "--template", str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def _explodes(url: str, timeout: float = 8.0) -> tuple[int | None, str, str]:
    raise AssertionError("an unresearched hole must not be fetched")


def test_an_unresearched_hole_is_missing_not_dead() -> None:
    """`dead` and `missing` call for opposite actions, and fetching "" cannot tell them apart."""
    verdict = P.stream_verdict("", fetch=_explodes)
    assert verdict.verdict == StreamKind.MISSING
    assert verdict.playable is False


def test_validate_reports_a_hole_without_calling_it_dead(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"presets": [
        {"buttonNumber": 1, "name": "Needs Research", "location": ""}]}), encoding="utf-8")
    assert P.main(["validate", "--template", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)["data"]["results"][0]
    assert (result["verdict"], result["name"]) == ("missing", "Needs Research")


@pytest.mark.parametrize("stream", ["https://radio.example.com/live?a=1&b=>?",
                                    "https://a.example/~~~"])
def test_a_base64url_blob_decodes_although_it_uses_the_url_alphabet(stream: str) -> None:
    """The cloud wrote its blob as base64url; the standard decoder drops '-' and '_' unread and
    returns a corrupt string rather than refusing."""
    blob = base64.urlsafe_b64encode(
        json.dumps({"name": "X", "imageUrl": "", "streamUrl": stream}).encode()).decode()
    assert "-" in blob or "_" in blob, "fixture must exercise the url alphabet"
    location = ("https://content.api.bose.io/core02/svc-bmx-adapter-orion/prod/orion/station?data="
                + urllib.parse.quote(blob.rstrip("="), safe=""))
    assert C.decode_cloud_location(location) == stream


def test_a_standard_blob_with_an_unescaped_plus_decodes() -> None:
    """A '+' left raw in the query string arrives as a space after query parsing."""
    stream = "https://a.example/live?>>>"
    blob = base64.b64encode(
        json.dumps({"name": "X", "imageUrl": "", "streamUrl": stream}).encode()).decode()
    assert "+" in blob, "fixture must carry a '+'"
    location = "https://content.api.bose.io/core02/svc-bmx-adapter-orion/prod/orion/station?data=" + blob
    assert C.decode_cloud_location(location) == stream


def test_harvest_of_an_unreadable_backup_answers_with_an_envelope(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = P.main(["harvest", "--backup", str(tmp_path / "absent.xml")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["ok"] is False and "absent.xml" in out["data"]["error"]


def test_harvest_without_out_prints_one_json_document(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    backup = tmp_path / "presets.xml"
    backup.write_text(PREMIGRATION, encoding="utf-8")
    P.main(["harvest", "--backup", str(backup)])
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "harvest"
    assert [p["buttonNumber"] for p in out["data"]["template"]["presets"]] == [1, 2, 3, 4]
