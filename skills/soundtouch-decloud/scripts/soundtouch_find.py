#!/usr/bin/env python3
"""Find the speakers and say what state each one is in.

    uv run scripts/soundtouch_find.py --service http://192.0.2.10:8000
    uv run scripts/soundtouch_find.py --ip 192.0.2.31
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from soundtouch_core import (API_PORT, SSH_PORT, TELNET_PORT, SpeakerError, cloud_leftovers,
                                 http_get, parse_presets, parse_sources, parse_urls, port_open,
                                 telnet_run)
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from soundtouch_core import (API_PORT, SSH_PORT, TELNET_PORT, SpeakerError, cloud_leftovers,
                                 http_get, parse_presets, parse_sources, parse_urls, port_open,
                                 telnet_run)

__all__ = ["build_parser", "classify", "describe_state", "speaker_state", "main"]


def classify(state: dict[str, object]) -> str:
    """One word for what to do about this speaker next."""
    ports = state.get("ports") or {}
    if not isinstance(ports, dict) or not ports.get(str(API_PORT)):
        return "not-answering"
    if state.get("cloud_leftovers"):
        return "needs-migration"
    if not state.get("account"):
        return "needs-account"
    sources = state.get("sources") or {}
    if isinstance(sources, dict) and any(v != "READY" for v in sources.values()):
        return "sources-not-ready"
    if not state.get("preset_count"):
        return "needs-presets"
    return "ready"


def describe_state(verdict: str) -> str:
    """What to tell a non-technical owner, in their words rather than ours."""
    return {
        "not-answering": "This speaker did not answer. Press a button on it to wake it, then try "
                         "again. If it still does not answer, check it is on the same network as "
                         "the service and not on a guest network.",
        "needs-migration": "This speaker is still trying to reach the Bose cloud, which no longer "
                           "exists. It needs its service addresses rewritten.",
        "needs-account": "This speaker has no account attached, so it will not load any radio at "
                         "all until one is bound to it.",
        "sources-not-ready": "This speaker has not finished loading its radio sources. If it was "
                             "just restarted, give it about 80 seconds and look again.",
        "needs-presets": "This speaker is working but has no presets on it yet.",
        "ready": "This speaker is set up and working.",
    }.get(verdict, verdict)


def speaker_state(ip: str) -> dict[str, object]:
    """Everything worth knowing about one speaker, without changing anything."""
    state: dict[str, object] = {"ip": ip}
    state["ports"] = {str(p): port_open(ip, p) for p in (SSH_PORT, TELNET_PORT, API_PORT)}
    if not state["ports"][str(API_PORT)]:
        state["verdict"] = "not-answering"
        state["advice"] = describe_state("not-answering")
        return state
    try:
        info = http_get(f"http://{ip}:{API_PORT}/info")
        state["name"] = info.split("<name>", 1)[1].split("</name>", 1)[0] if "<name>" in info else None
        state["device_id"] = info.split('deviceID="', 1)[1].split('"', 1)[0] if 'deviceID="' in info else None
        state["account"] = (info.split("<margeAccountUUID>", 1)[1].split("</", 1)[0]
                            if "<margeAccountUUID>" in info else "")
    except SpeakerError as exc:
        state["info_error"] = str(exc)
    if state["ports"][str(TELNET_PORT)]:
        try:
            urls = parse_urls(telnet_run(ip, ["getpdo CurrentSystemConfiguration"])[0]["reply"])
            state["urls"] = urls
            state["cloud_leftovers"] = cloud_leftovers(urls)
        except SpeakerError as exc:
            state["telnet_error"] = str(exc)
    try:
        state["sources"] = parse_sources(http_get(f"http://{ip}:{API_PORT}/sources"))
    except SpeakerError as exc:
        state["sources_error"] = str(exc)
    try:
        state["preset_count"] = len(parse_presets(http_get(f"http://{ip}:{API_PORT}/presets")))
    except SpeakerError as exc:
        state["presets_error"] = str(exc)
    state["verdict"] = classify(state)
    state["advice"] = describe_state(str(state["verdict"]))
    return state


def _discover(service: str) -> list[dict[str, object]]:
    body = http_get(f"{service.rstrip('/')}/api/setup/devices")
    try:
        found = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SpeakerError("the service answered but not with JSON") from exc
    return [d for d in found if isinstance(d, dict)]


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", help="AfterTouch base URL, to discover speakers")
    parser.add_argument("--ip", action="append", default=[], help="check this speaker directly")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.service and not args.ip:
        parser.error("give --service to discover, or --ip to check one speaker")

    data: dict[str, object] = {}
    targets = list(args.ip)
    if args.service:
        try:
            devices = _discover(args.service)
        except SpeakerError as exc:
            print(json.dumps({"ok": False, "command": "find", "data": {"error": str(exc)}}, indent=2))
            return 2
        data["discovered"] = [{"name": d.get("name"), "device_id": d.get("device_id"),
                               "ip": d.get("ip_address")} for d in devices]
        targets += [str(d.get("ip_address")) for d in devices if d.get("ip_address")]

    seen: list[str] = []
    states = []
    for ip in targets:
        if ip in seen:
            continue
        seen.append(ip)
        states.append(speaker_state(ip))
    data["speakers"] = states
    ok = bool(states) and all(s.get("verdict") != "not-answering" for s in states)
    if not ok:
        data["next"] = ("At least one speaker did not answer. Ask the owner to press a button on "
                        "it, then run this again.")
    print(json.dumps({"ok": ok, "command": "find", "data": data}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
