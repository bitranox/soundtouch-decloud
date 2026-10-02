#!/usr/bin/env python3
"""Back up, harvest, validate, check and restore a speaker's presets.

    uv run scripts/soundtouch_presets.py harvest  --backup before.xml --out speaker.json
    uv run scripts/soundtouch_presets.py validate --template speaker.json
    uv run scripts/soundtouch_presets.py backup  --ip 192.0.2.31 --outdir ./backup
    uv run scripts/soundtouch_presets.py check   --ip 192.0.2.31 --template speaker.json
    uv run scripts/soundtouch_presets.py restore --ip 192.0.2.31 --template speaker.json --confirm
    uv run scripts/soundtouch_presets.py restore --ip 192.0.2.31 --template speaker.json \
                                                 --service http://192.0.2.10:8000 --absolute --confirm
    uv run scripts/soundtouch_presets.py relativize --ip 192.0.2.31 --outdir ./backup --confirm

Presets are written in the relative Orion form, which follows the service to a new address;
--absolute writes the host in, for firmware that cannot resolve a relative location. `relativize`
stores every host-bound preset on a speaker (absolute Orion, or the legacy /custom/v1/playback form)
again in the relative form; `check` and `restore` name those buttons under "host_bound".
Nothing is written without --confirm.
Every subcommand prints a JSON envelope: exit 0 yes, 1 no, 2 error.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

try:
    from soundtouch_core import (API_PORT, PLAYBACK_PATH, ContentItemType, PresetEntry,
                                 RadioSource, SpeakerError, StreamKind, classify_stream,
                                 harvest_presets, http_get, orion_location, parse_presets,
                                 parse_sources, playlist_targets, relative_orion_location,
                                 relativize_plan, slots_to_write)
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from soundtouch_core import (API_PORT, PLAYBACK_PATH, ContentItemType, PresetEntry,
                                 RadioSource, SpeakerError, StreamKind, classify_stream,
                                 harvest_presets, http_get, orion_location, parse_presets,
                                 parse_sources, playlist_targets, relative_orion_location,
                                 relativize_plan, slots_to_write)

REQUIRED_FIELDS = ("buttonNumber", "name", "location")
# A kept entry (an album, a library track) is not a stream, so fetching it proves nothing. It is
# reported as what it is instead of being counted as unplayable, which would fail every validate.
KEPT_NOTE = "not radio; left as it is"
# Since AfterTouch v0.137.0 a preset written to one speaker is passed on to the other speakers of
# its account, so a restore is never as local as the one --ip it names.
SHARING_NOTE = ("AfterTouch shares a stored preset with the other speakers of the same account "
                "(v0.137.0 and later), so this write can reach them too.")
NOT_MOUNTED = ("the radio source is not mounted yet, so a write would be silently undone. Wait "
               "about 80 seconds after a restart.")

__all__ = ["Command", "Endpoint", "Template", "TemplateRow", "StreamCheck", "ValidationResult", "Backup",
           "BackupFile", "build_parser", "load_template", "load_partial_template", "radio_ready",
           "preset_xml", "stream_verdict", "main"]


class Command(StrEnum):
    BACKUP = "backup"
    CHECK = "check"
    RESTORE = "restore"
    RELATIVIZE = "relativize"
    HARVEST = "harvest"
    VALIDATE = "validate"


class Endpoint(StrEnum):
    """The speaker endpoints a backup saves, one file each, in this order."""

    PRESETS = "presets"
    INFO = "info"
    RECENTS = "recents"
    SOURCES = "sources"


class Emit(Protocol):
    """main's envelope printer: 1 is a definite no, 2 is a question left unanswered."""

    def __call__(self, ok: bool, data: dict[str, object], code: int = 1) -> int: ...


@dataclass(frozen=True)
class Template:
    """A preset template as `harvest` writes it."""

    device_id: str
    name: str
    presets: tuple[PresetEntry, ...]

    def to_json(self) -> dict[str, object]:
        return {"deviceId": self.device_id, "name": self.name,
                "presets": [entry.to_json() for entry in self.presets]}


@dataclass(frozen=True)
class TemplateRow:
    """One row of a template read for `validate`, which must accept the holes `harvest` leaves.

    The button and the name are echoed back exactly as the file holds them, whatever their type:
    `validate` reports on a template, it does not correct one.
    """

    button_number: object
    name: object
    location: str
    keep: bool


@dataclass(frozen=True)
class StreamCheck:
    """Whether a preset built on one URL will actually play, and what came back instead if not."""

    url: str
    status: int | None
    content_type: str
    verdict: StreamKind
    resolved_to: str | None = None

    @property
    def playable(self) -> bool:
        return self.verdict == StreamKind.AUDIO

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"url": self.url, "status": self.status,
                                  "content_type": self.content_type,
                                  "verdict": self.verdict.value, "playable": self.playable}
        if self.resolved_to is not None:
            out["resolved_to"] = self.resolved_to
        return out


@dataclass(frozen=True)
class ValidationResult:
    """One template row and what its stream turned out to be; `check` is None for a kept row."""

    row: TemplateRow
    check: StreamCheck | None

    @property
    def playable(self) -> bool:
        return self.check is None or self.check.playable

    def to_json(self) -> dict[str, object]:
        verdict: dict[str, object] = (
            {"verdict": StreamKind.KEPT.value, "playable": True, "note": KEPT_NOTE}
            if self.check is None else self.check.to_json())
        return {"buttonNumber": self.row.button_number, "name": self.row.name, **verdict}


@dataclass(frozen=True)
class BackupFile:
    """One endpoint saved to disk, or the reason it could not be read."""

    endpoint: Endpoint
    path: str = ""
    error: str = ""

    def to_json(self) -> str:
        return self.path if not self.error else f"FAILED: {self.error}"


@dataclass(frozen=True)
class Backup:
    files: tuple[BackupFile, ...]

    @property
    def presets_path(self) -> str:
        """Where the presets went, or "" when that read failed - the one file a write depends on."""
        return next((f.path for f in self.files if f.endpoint == Endpoint.PRESETS and not f.error), "")

    def to_json(self) -> dict[str, str]:
        return {f.endpoint.value: f.to_json() for f in self.files}


def _read_json(path: str) -> object:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _preset_list(data: object) -> list[object]:
    presets = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(presets, list):
        raise ValueError("template has no presets")
    return presets


def _entry(raw: object, seen: set[int]) -> PresetEntry:
    """One template entry, validated; `seen` collects the buttons so a repeat is refused."""
    for field in REQUIRED_FIELDS:
        if not isinstance(raw, dict) or field not in raw:
            raise ValueError(f"preset is missing '{field}': {raw}")
    assert isinstance(raw, dict)  # narrowed by the loop above, which raised for anything else
    button = raw["buttonNumber"]
    if not isinstance(button, int) or not 1 <= button <= 6:
        raise ValueError(f"buttonNumber must be 1..6, got {button!r}")
    if button in seen:
        raise ValueError(f"buttonNumber {button} appears twice")
    seen.add(button)
    entry = PresetEntry(button_number=button, name=str(raw["name"]), location=str(raw["location"]),
                        content_item_type=str(raw.get("contentItemType", ContentItemType.STATION_URL)),
                        source=str(raw.get("source", RadioSource.LOCAL_INTERNET_RADIO)),
                        keep=bool(raw.get("keep")))
    if entry.keep:
        return entry  # not radio: carried as stored, never written, so there is no stream to demand
    if PLAYBACK_PATH in entry.location or relative_orion_location(entry.location):
        raise ValueError("location must be the PLAIN stream URL; the wrapping is added on write")
    if not entry.location.startswith(("http://", "https://")):
        raise ValueError(
            f"button {button} ({raw.get('name')!r}) has no stream URL yet. `harvest` leaves a "
            f"hole for every station whose stream it could not recover; find the station's "
            f"current stream, put it here, and confirm it with `validate` before writing")
    return entry


def load_template(path: str) -> list[PresetEntry]:
    """Read and validate a preset template.

    Validated rather than trusted because a template whose location already carries the playback
    adapter would be double-wrapped, and one missing a button number silently writes nothing.
    """
    presets = _preset_list(_read_json(path))
    if not presets:
        raise ValueError("template has no presets")
    seen: set[int] = set()
    return [_entry(raw, seen) for raw in presets]


def load_partial_template(path: str) -> list[TemplateRow]:
    """Read a template WITHOUT demanding every hole be filled.

    `validate` has to be able to read the very file `harvest` just wrote, holes and all - that is
    the file whose holes it exists to report. Only the paths that WRITE to a speaker use the strict
    reader.
    """
    rows: list[TemplateRow] = []
    for raw in _preset_list(_read_json(path)):
        if not isinstance(raw, dict):
            raise ValueError(f"preset is not an object: {raw}")
        rows.append(TemplateRow(button_number=raw.get("buttonNumber"), name=raw.get("name"),
                                location=str(raw.get("location", "")), keep=bool(raw.get("keep"))))
    return rows


def radio_ready(ip: str) -> bool:
    """Has the speaker mounted the radio source yet?

    Writing presets before it has is silently undone by the same boot-time wipe they are meant to
    survive, so a restore run must do nothing at all in that window.
    """
    try:
        sources = parse_sources(http_get(f"http://{ip}:{API_PORT}/sources"))
    except SpeakerError:
        return False
    return sources.is_ready(RadioSource.LOCAL_INTERNET_RADIO)


def preset_xml(entry: PresetEntry, *, service: str = "") -> str:
    """The body for one preset slot, with the location wrapped the way AfterTouch's player does.

    Relative unless `service` is given; see orion_location for when the absolute form is wanted.
    """
    location = orion_location(entry.location, entry.name, service=service)
    name = entry.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<ContentItem source="{entry.source}" type="{entry.content_item_type}" '
            f'location="{location.replace("&", "&amp;")}" sourceAccount="" isPresetable="true">'
            f"<itemName>{name}</itemName></ContentItem>")


def _post_preset(ip: str, body: str) -> None:
    """POST one `<preset id="N">...</preset>` body to the speaker's storePreset."""
    req = urllib.request.Request(f"http://{ip}:{API_PORT}/storePreset", data=body.encode(),
                                 method="POST")
    with urllib.request.urlopen(req, timeout=15):  # noqa: S310 - fixed http URL built above
        pass


def _store(ip: str, entry: PresetEntry, *, service: str = "") -> None:
    _post_preset(ip, f'<preset id="{entry.button_number}">{preset_xml(entry, service=service)}</preset>')


def _backup(ip: str, outdir: str) -> Backup:
    """Save what the speaker reports now, one file per endpoint; a failed read is recorded, not raised."""
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    files: list[BackupFile] = []
    for endpoint in Endpoint:
        try:
            body = http_get(f"http://{ip}:{API_PORT}/{endpoint}")
        except SpeakerError as exc:
            files.append(BackupFile(endpoint, error=str(exc)))
            continue
        path = out / f"{ip}-{stamp}-{endpoint}.xml"
        path.write_text(body, encoding="utf-8")
        files.append(BackupFile(endpoint, path=str(path)))
    return Backup(tuple(files))


Fetch = Callable[[str], tuple[int | None, str, str]]


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


def stream_verdict(url: str, *, fetch: Fetch = _fetch_head, depth: int = 1) -> StreamCheck:
    """Whether a preset built on this URL will actually play, and if not, what came back instead.

    A playlist is followed exactly ONE level, because a station's published "stream link" is very
    often an .m3u or .pls listing the real endpoint. Following further would start walking the open
    web on the owner's behalf.

    A hole that `harvest` left is reported as `missing`, never as `dead`. They call for opposite
    actions - `dead` means this station moved or ended and needs a replacement stream, `missing`
    means nobody has looked for one yet - and a fetch of an empty string cannot tell them apart.
    """
    if not url.strip():
        return StreamCheck(url=url, status=None, content_type="", verdict=StreamKind.MISSING)
    status, ctype, head = fetch(url)
    kind = classify_stream(status, ctype, head)
    if kind == StreamKind.PLAYLIST and depth > 0:
        targets = playlist_targets(head)
        if targets:
            inner = stream_verdict(targets[0], fetch=fetch, depth=depth - 1)
            return dataclasses.replace(inner, url=url, resolved_to=targets[0])
    return StreamCheck(url=url, status=status, content_type=ctype, verdict=kind)


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in (Command.BACKUP, Command.CHECK, Command.RESTORE, Command.RELATIVIZE):
        p = sub.add_parser(name)
        p.add_argument("--ip", required=True)
        if name in (Command.BACKUP, Command.RELATIVIZE):
            p.add_argument("--outdir", default=".", help="where the backup taken first is saved"
                           if name == Command.RELATIVIZE else "where the backup is saved")
        else:
            p.add_argument("--template", required=True)
            p.add_argument("--service", default="",
                           help="the service base URL; only used with --absolute")
        if name == Command.RESTORE:
            p.add_argument("--absolute", action="store_true",
                           help="write the service host into each location (needs --service), "
                                "for firmware that cannot resolve the relative form")
        if name in (Command.RESTORE, Command.RELATIVIZE):
            p.add_argument("--confirm", action="store_true",
                           help="required: without it nothing is written")
    h = sub.add_parser(Command.HARVEST, help="turn a saved presets XML into a template, holes and all")
    h.add_argument("--backup", required=True, help="a presets XML from `backup`, or a synced Presets.xml "
                                                  "from the service data dir")
    h.add_argument("--out", help="write the template here instead of stdout")
    h.add_argument("--name", default="", help="speaker name to record in the template")
    h.add_argument("--device-id", default="", help="device id to record in the template")
    v = sub.add_parser(Command.VALIDATE, help="fetch every stream in a template and say if it plays")
    v.add_argument("--template", required=True)
    return parser


def _harvest(args: argparse.Namespace, emit: Emit) -> int:
    raw = pathlib.Path(args.backup).read_text(encoding="utf-8")
    entries = harvest_presets(parse_presets(raw))
    holes = [e for e in entries if not e.location]
    body = json.dumps(Template(args.device_id, args.name, tuple(entries)).to_json(), indent=2,
                      ensure_ascii=False)
    if args.out:
        pathlib.Path(args.out).write_text(body + "\n", encoding="utf-8")
    else:
        print(body)
    return emit(not holes, {"presets": len(entries), "unresolved": len(holes),
                            "needs_research": [e.name for e in holes],
                            "kept": [e.name for e in entries if e.keep],
                            "out": args.out or "(stdout)"})


def _validate(args: argparse.Namespace, emit: Emit) -> int:
    try:
        rows = load_partial_template(args.template)
    except (OSError, ValueError) as exc:
        return emit(False, {"error": str(exc)}, code=2)
    checked = [ValidationResult(row, None if row.keep else stream_verdict(row.location))
               for row in rows]
    bad = [c for c in checked if not c.playable]
    return emit(not bad, {"checked": len(checked), "unplayable": len(bad),
                          "results": [c.to_json() for c in checked]})


def _check_or_restore(args: argparse.Namespace, cmd: Command, emit: Emit) -> int:
    if cmd == Command.RESTORE and args.absolute and not args.service:
        return emit(False, {"error": "--absolute writes the service host into each preset, so it "
                                     "needs --service"}, code=2)
    try:
        wanted = load_template(args.template)
    except (OSError, ValueError) as exc:
        return emit(False, {"error": str(exc)}, code=2)
    try:
        current = http_get(f"http://{args.ip}:{API_PORT}/presets")
    except SpeakerError as exc:
        return emit(False, {"error": str(exc)}, code=2)
    todo = slots_to_write(parse_presets(current), wanted)

    if cmd == Command.CHECK:
        return emit(not todo, {"wanted": len(wanted), "missing": len(todo),
                               "missing_streams": [p.location for p in todo],
                               "buttons": [p.button_number for p in todo],
                               **_host_bound_report(current)})
    if not todo:
        return emit(True, {"wrote": 0, "note": "already correct", **_host_bound_report(current)})
    if not radio_ready(args.ip):
        return emit(False, {"error": NOT_MOUNTED})
    if not args.confirm:
        return emit(False, {"would_write": len(todo),
                            "missing_streams": [p.location for p in todo],
                            "buttons": [p.button_number for p in todo],
                            "note": "re-run with --confirm to write these",
                            "sharing": SHARING_NOTE})
    return _restore(args, todo, wanted, emit)


def _restore(args: argparse.Namespace, todo: Sequence[PresetEntry], wanted: Sequence[PresetEntry],
             emit: Emit) -> int:
    wrote: list[str] = []
    for entry in sorted(todo, key=lambda p: p.button_number):
        try:
            _store(args.ip, entry, service=args.service if args.absolute else "")
            wrote.append(entry.name)
        except OSError as exc:
            return emit(False, {"wrote": wrote, "error": f"{entry.name}: {exc}"}, code=2)
        time.sleep(0.5)
    after = slots_to_write(parse_presets(http_get(f"http://{args.ip}:{API_PORT}/presets")), wanted)
    return emit(not after, {"wrote": wrote, "still_missing": [p.location for p in after],
                            "sharing": SHARING_NOTE})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = Command(args.cmd)

    def emit(ok: bool, data: dict[str, object], code: int = 1) -> int:
        """One JSON envelope on every path. 1 is a definite no, 2 is a question left unanswered."""
        print(json.dumps({"ok": ok, "command": cmd.value, "data": data}, indent=2))
        return 0 if ok else code

    if cmd == Command.HARVEST:
        return _harvest(args, emit)
    if cmd == Command.VALIDATE:
        return _validate(args, emit)
    if cmd == Command.BACKUP:
        saved = _backup(args.ip, args.outdir)
        return emit(bool(saved.presets_path), {"saved": saved.to_json()}, code=2)
    if cmd == Command.RELATIVIZE:
        return _relativize(args.ip, args.outdir, confirm=args.confirm, emit=emit)
    return _check_or_restore(args, cmd, emit)


def _host_bound_report(current: str) -> dict[str, object]:
    """Which buttons name the service's address, as a warning that does not fail the check.

    The station on such a button is right, so the check passes; it is the location that stops
    playing once the service moves, and `relativize` is what fixes it. Returns the envelope keys.
    """
    buttons = [step.button for step in relativize_plan(current)]
    if not buttons:
        return {"host_bound": []}
    return {"host_bound": buttons,
            "warning": f"buttons {buttons} name the service's address and stop playing when it "
                       f"moves; `relativize` stores them again in the relative form"}


def _relativize(ip: str, outdir: str, *, confirm: bool, emit: Emit) -> int:
    """Store every host-bound preset on this speaker again in the relative form.

    The same station, name, art and button; only the location loses its host, so the preset follows
    the service instead of naming the address it had when it was saved. AfterTouch's Health page
    offers the same fix. Backed up first, and refused in the boot window like any other write.
    """
    try:
        plan = relativize_plan(http_get(f"http://{ip}:{API_PORT}/presets"))
    except SpeakerError as exc:
        return emit(False, {"error": str(exc)}, code=2)
    buttons = [step.button for step in plan]
    if not plan:
        return emit(True, {"rewrote": [], "note": "no radio preset names the service's address"})
    if not confirm:
        return emit(False, {"would_rewrite": buttons, "note": "re-run with --confirm to write these",
                            "sharing": SHARING_NOTE})
    if not radio_ready(ip):
        return emit(False, {"error": NOT_MOUNTED})
    saved = _backup(ip, outdir)
    if not saved.presets_path:
        return emit(False, {"error": "could not back the presets up first, so nothing was written",
                            "saved": saved.to_json()}, code=2)
    rewrote: list[int] = []
    for step in plan:
        try:
            _post_preset(ip, step.body)
        except OSError as exc:
            return emit(False, {"rewrote": rewrote, "error": f"button {step.button}: {exc}",
                                "backup": saved.presets_path}, code=2)
        rewrote.append(step.button)
        time.sleep(0.5)
    left = [step.button for step in relativize_plan(http_get(f"http://{ip}:{API_PORT}/presets"))]
    return emit(not left, {"rewrote": rewrote, "still_absolute": left, "backup": saved.presets_path,
                           "sharing": SHARING_NOTE})


if __name__ == "__main__":
    sys.exit(main())
