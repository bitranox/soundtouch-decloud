#!/usr/bin/env python3
"""Back up, harvest, validate, check and restore a speaker's presets.

    uv run scripts/soundtouch_presets.py harvest  --backup before.xml --out speaker.json
    uv run scripts/soundtouch_presets.py validate --template speaker.json
    uv run scripts/soundtouch_presets.py backup  --ip 192.0.2.31 --outdir ./backup
    uv run scripts/soundtouch_presets.py check   --ip 192.0.2.31 --template speaker.json \
                                                 --service http://192.0.2.10:8000
    uv run scripts/soundtouch_presets.py restore --ip 192.0.2.31 --template speaker.json \
                                                 --service http://192.0.2.10:8000 --confirm

Nothing is written without --confirm.
Every subcommand prints a JSON envelope: exit 0 yes, 1 no, 2 error.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

try:
    from soundtouch_core import (API_PORT, SpeakerError, classify_stream, harvest_presets,
                                 http_get, parse_sources, playback_location, playlist_targets,
                                 slots_to_write)
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from soundtouch_core import (API_PORT, SpeakerError, classify_stream, harvest_presets,
                                 http_get, parse_sources, playback_location, playlist_targets,
                                 slots_to_write)

REQUIRED_FIELDS = ("buttonNumber", "name", "location")

__all__ = ["build_parser", "load_template", "load_partial_template", "radio_ready",
           "preset_xml", "stream_verdict", "main"]


def load_template(path: str) -> dict[str, object]:
    """Read and validate a preset template.

    Validated rather than trusted because a template whose location already carries the playback
    adapter would be double-wrapped, and one missing a button number silently writes nothing.
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    presets = data.get("presets")
    if not isinstance(presets, list) or not presets:
        raise ValueError("template has no presets")
    seen: set[int] = set()
    for entry in presets:
        for field in REQUIRED_FIELDS:
            if field not in entry:
                raise ValueError(f"preset is missing '{field}': {entry}")
        button = entry["buttonNumber"]
        if not isinstance(button, int) or not 1 <= button <= 6:
            raise ValueError(f"buttonNumber must be 1..6, got {button!r}")
        if button in seen:
            raise ValueError(f"buttonNumber {button} appears twice")
        seen.add(button)
        if "/custom/v1/playback/" in entry["location"]:
            raise ValueError("location must be the PLAIN stream URL; the wrapping is added on write")
        if not str(entry["location"]).startswith(("http://", "https://")):
            raise ValueError(
                f"button {button} ({entry.get('name')!r}) has no stream URL yet. `harvest` leaves a "
                f"hole for every station whose stream it could not recover; find the station's "
                f"current stream, put it here, and confirm it with `validate` before writing")
    return data


def load_partial_template(path: str) -> dict[str, object]:
    """Read a template WITHOUT demanding every hole be filled.

    `validate` has to be able to read the very file `harvest` just wrote, holes and all - that is
    the file whose holes it exists to report. Only the paths that WRITE to a speaker use the strict
    reader.
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("presets"), list):
        raise ValueError("template has no presets")
    return data


def radio_ready(ip: str) -> bool:
    """Has the speaker mounted the radio source yet?

    Writing presets before it has is silently undone by the same boot-time wipe they are meant to
    survive, so a restore run must do nothing at all in that window.
    """
    try:
        return parse_sources(http_get(f"http://{ip}:{API_PORT}/sources"))["LOCAL_INTERNET_RADIO"] == "READY"
    except (SpeakerError, KeyError):
        return False


def preset_xml(service: str, entry: dict[str, object]) -> str:
    """The body for one preset slot, with the location wrapped for the playback adapter."""
    location = playback_location(service, str(entry["location"]), str(entry["name"]))
    name = str(entry["name"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<ContentItem source="{entry.get("source", "LOCAL_INTERNET_RADIO")}" '
            f'type="{entry.get("contentItemType", "stationurl")}" '
            f'location="{location.replace("&", "&amp;")}" sourceAccount="" isPresetable="true">'
            f"<itemName>{name}</itemName></ContentItem>")


def _store(ip: str, service: str, entry: dict[str, object]) -> None:
    url = f"http://{ip}:{API_PORT}/storePreset"
    body = (f'<preset id="{entry["buttonNumber"]}">{preset_xml(service, entry)}</preset>').encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15):  # noqa: S310 - fixed http URL built above
        pass


def _fetch_head(url: str, timeout: float = 8.0) -> tuple[int | None, str, str]:
    """Ask a candidate stream URL what it is, reading only the first couple of kilobytes.

    Every failure is a return value rather than an exception: this runs over a list of URLs and one
    dead station must not stop the rest being reported.
    """
    if not url.startswith(("http://", "https://")):
        return None, "", ""
    req = urllib.request.Request(url, headers={"User-Agent": "SoundTouch", "Icy-MetaData": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
            head = resp.read(2048).decode("utf-8", "replace")
            return resp.status, resp.headers.get("Content-Type", ""), head
    except urllib.error.HTTPError as exc:
        return exc.code, "", ""
    except (OSError, ValueError):
        return None, "", ""


def stream_verdict(url: str, *, fetch=_fetch_head, depth: int = 1) -> dict[str, object]:
    """Whether a preset built on this URL will actually play, and if not, what came back instead.

    A playlist is followed exactly ONE level, because a station's published "stream link" is very
    often an .m3u or .pls listing the real endpoint. Following further would start walking the open
    web on the owner's behalf.

    A hole that `harvest` left is reported as `missing`, never as `dead`. They call for opposite
    actions - `dead` means this station moved or ended and needs a replacement stream, `missing`
    means nobody has looked for one yet - and a fetch of an empty string cannot tell them apart.
    """
    if not url.strip():
        return {"url": url, "status": None, "content_type": "", "verdict": "missing",
                "playable": False}
    status, ctype, head = fetch(url)
    kind = classify_stream(status, ctype, head)
    if kind == "playlist" and depth > 0:
        targets = playlist_targets(head)
        if targets:
            inner = stream_verdict(targets[0], fetch=fetch, depth=depth - 1)
            return {**inner, "url": url, "resolved_to": targets[0]}
    return {"url": url, "status": status, "content_type": ctype, "verdict": kind,
            "playable": kind == "audio"}


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("backup", "check", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--ip", required=True)
        if name == "backup":
            p.add_argument("--outdir", default=".")
        else:
            p.add_argument("--template", required=True)
            p.add_argument("--service", required=True)
        if name == "restore":
            p.add_argument("--confirm", action="store_true",
                           help="required: without it nothing is written")
    h = sub.add_parser("harvest", help="turn a saved presets XML into a template, holes and all")
    h.add_argument("--backup", required=True, help="a presets XML from `backup`, or the service's "
                                                  "<MAC>-presets-before-migration.xml")
    h.add_argument("--out", help="write the template here instead of stdout")
    h.add_argument("--name", default="", help="speaker name to record in the template")
    h.add_argument("--device-id", default="", help="device id to record in the template")
    v = sub.add_parser("validate", help="fetch every stream in a template and say if it plays")
    v.add_argument("--template", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def emit(ok: bool, data: dict[str, object], code: int = 1) -> int:
        """One JSON envelope on every path. 1 is a definite no, 2 is a question left unanswered."""
        print(json.dumps({"ok": ok, "command": args.cmd, "data": data}, indent=2))
        return 0 if ok else code

    if args.cmd == "harvest":
        raw = pathlib.Path(args.backup).read_text(encoding="utf-8")
        entries = harvest_presets(raw)
        holes = [e for e in entries if not e["location"]]
        template = {"deviceId": args.device_id, "name": args.name, "presets": entries}
        body = json.dumps(template, indent=2)
        if args.out:
            pathlib.Path(args.out).write_text(body + "\n", encoding="utf-8")
        else:
            print(body)
        return emit(not holes, {"presets": len(entries), "unresolved": len(holes),
                                "needs_research": [e["name"] for e in holes],
                                "out": args.out or "(stdout)"})

    if args.cmd == "validate":
        try:
            template = load_partial_template(args.template)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return emit(False, {"error": str(exc)}, code=2)
        checked = [{"buttonNumber": e.get("buttonNumber"), "name": e.get("name"),
                    **stream_verdict(str(e.get("location", "")))}
                   for e in template["presets"]]  # type: ignore[union-attr]
        bad = [c for c in checked if not c["playable"]]
        return emit(not bad, {"checked": len(checked), "unplayable": len(bad), "results": checked})

    if args.cmd == "backup":
        out = pathlib.Path(args.outdir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        saved: dict[str, object] = {}
        for endpoint in ("presets", "info", "recents", "sources"):
            try:
                body = http_get(f"http://{args.ip}:{API_PORT}/{endpoint}")
            except SpeakerError as exc:
                saved[endpoint] = f"FAILED: {exc}"
                continue
            path = out / f"{args.ip}-{stamp}-{endpoint}.xml"
            path.write_text(body, encoding="utf-8")
            saved[endpoint] = str(path)
        ok = isinstance(saved.get("presets"), str) and not str(saved["presets"]).startswith("FAILED")
        return emit(ok, {"saved": saved}, code=2)

    try:
        template = load_template(args.template)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return emit(False, {"error": str(exc)}, code=2)
    try:
        current = http_get(f"http://{args.ip}:{API_PORT}/presets")
    except SpeakerError as exc:
        return emit(False, {"error": str(exc)}, code=2)

    presets = list(template["presets"])  # type: ignore[arg-type]
    todo = slots_to_write(current, presets)  # type: ignore[arg-type]

    if args.cmd == "check":
        return emit(not todo, {"wanted": len(presets), "missing": len(todo),
                               "missing_streams": [p["location"] for p in todo],
                               "buttons": [p["buttonNumber"] for p in todo]})

    if not todo:
        return emit(True, {"wrote": 0, "note": "already correct"})
    if not radio_ready(args.ip):
        return emit(False, {"error": "the radio source is not mounted yet, so a write would be "
                                     "silently undone. Wait about 80 seconds after a restart."})
    if not args.confirm:
        return emit(False, {"would_write": len(todo),
                            "missing_streams": [p["location"] for p in todo],
                            "buttons": [p["buttonNumber"] for p in todo],
                            "note": "re-run with --confirm to write these"})
    wrote = []
    for entry in sorted(todo, key=lambda p: p["buttonNumber"]):
        try:
            _store(args.ip, args.service, entry)  # type: ignore[arg-type]
            wrote.append(entry["name"])
        except OSError as exc:
            return emit(False, {"wrote": wrote, "error": f"{entry['name']}: {exc}"}, code=2)
        time.sleep(0.5)
    after = slots_to_write(http_get(f"http://{args.ip}:{API_PORT}/presets"), presets)  # type: ignore[arg-type]
    return emit(not after, {"wrote": wrote, "still_missing": [p["location"] for p in after]})


if __name__ == "__main__":
    sys.exit(main())
