#!/usr/bin/env python3
"""Take one speaker onto the local service, or report where it stands.

    uv run scripts/soundtouch_onboard.py --ip 192.0.2.31 state
    uv run scripts/soundtouch_onboard.py --ip 192.0.2.31 --service http://192.0.2.10:8000 migrate --confirm
    uv run scripts/soundtouch_onboard.py --ip 192.0.2.31 \
                                         --service http://192.0.2.10:8000 enable-ssh --confirm
    uv run scripts/soundtouch_onboard.py --ip 192.0.2.31 reboot --confirm
    uv run scripts/soundtouch_onboard.py --ip 192.0.2.31 play --preset 1 \
                                         --expect "Example Radio" --confirm

Nothing that changes the speaker runs without --confirm.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

try:
    from soundtouch_core import (API_PORT, SSH_PORT, RadioSources, ServiceUrls, SpeakerError,
                                 build_enable_ssh_commands, build_url_commands, http_get,
                                 parse_sources, parse_urls, port_open, telnet_run)
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from soundtouch_core import (API_PORT, SSH_PORT, RadioSources, ServiceUrls, SpeakerError,
                                 build_enable_ssh_commands, build_url_commands, http_get,
                                 parse_sources, parse_urls, port_open, telnet_run)

__all__ = ["Command", "MigrationVerdict", "NowPlaying", "build_parser", "migration_verdict",
           "account_uuid", "wait_down", "wait_up", "wait_port", "main"]

# How long to keep asking for port 22 after the injection is written. The default form
# only stores the value, so a unit that fires it at the next boot cannot answer inside any
# window; the short wait is for the units that fire on their next read cycle. The full
# form reboots itself, and sshd comes up well after the API port.
SSH_WAIT_STORED = 30.0
SSH_WAIT_FULL = 150.0


class Command(StrEnum):
    """The subcommands, spelled as the user types them and as the envelope reports them."""

    STATE = "state"
    MIGRATE = "migrate"
    REBOOT = "reboot"
    ENABLE_SSH = "enable-ssh"
    PLAY = "play"


class NowPlayingSource(StrEnum):
    """The now_playing source word this script acts on; every other source is firmware text."""

    STANDBY = "STANDBY"


class PlayStatus(StrEnum):
    """The playStatus word that means audio is running; every other status is firmware text."""

    PLAY_STATE = "PLAY_STATE"


class KeyState(StrEnum):
    """A /key press is two posts, and the speaker acts on the release."""

    PRESS = "press"
    RELEASE = "release"


class Key(StrEnum):
    """The fixed key names this script presses; preset keys are built by preset_key."""

    POWER = "POWER"


def preset_key(button: int) -> str:
    """The key name for one preset button, as the speaker spells it."""
    return f"PRESET_{button}"


@dataclass(frozen=True)
class NowPlaying:
    """One /now_playing reading.

    The source defaults to "" and the status and item to "-" when the speaker left the field out,
    so a reading with nothing in it can never equal STANDBY, PLAY_STATE or a station name.
    """

    source: str
    play_status: str
    item_name: str

    @property
    def is_standby(self) -> bool:
        return self.source == NowPlayingSource.STANDBY

    @property
    def is_playing(self) -> bool:
        return self.play_status == PlayStatus.PLAY_STATE


@dataclass(frozen=True)
class MigrationVerdict:
    """Is this speaker fully migrated, and if not, what is wrong with it?"""

    urls: ServiceUrls

    @property
    def ok(self) -> bool:
        """An empty read is not a pass: nothing read means nothing proven."""
        return (bool(self.urls) and not self.urls.cloud_leftovers()
                and not self.urls.injected() and not self.urls.missing())

    def to_json(self) -> dict[str, object]:
        return {
            "urls": self.urls.to_json(),
            "cloud_leftovers": self.urls.cloud_leftovers().to_json(),
            "still_injected": self.urls.injected().to_json(),
            "missing": [field.value for field in self.urls.missing()],
            "ok": self.ok,
        }


@dataclass(frozen=True)
class Options:
    """The parsed command line; subcommand-only options hold their neutral value elsewhere."""

    ip: str
    service: str | None
    command: Command
    confirm: bool
    sources_wait: float
    full_config: bool
    assume_paired: bool
    preset: int
    expect: str
    wait: float

    @classmethod
    def from_namespace(cls, ns: argparse.Namespace) -> Options:
        return cls(ip=str(ns.ip), service=ns.service, command=Command(ns.cmd),
                   confirm=bool(getattr(ns, "confirm", False)),
                   sources_wait=float(getattr(ns, "sources_wait", 0.0)),
                   full_config=bool(getattr(ns, "full_config", False)),
                   assume_paired=bool(getattr(ns, "assume_paired", False)),
                   preset=int(getattr(ns, "preset", 0)),
                   expect=str(getattr(ns, "expect", "")),
                   wait=float(getattr(ns, "wait", 0.0)))


def parse_account_uuid(info: str) -> str:
    """The bound account out of an /info body, or "" when the field is absent."""
    if "<margeAccountUUID>" not in info:
        return ""
    return info.split("<margeAccountUUID>", 1)[1].split("</", 1)[0].strip()


def parse_volume(raw: str) -> int | None:
    """The actual volume out of a /volume body, or None when it is absent or not a number."""
    value = raw.split("<actualvolume>", 1)[1].split("<", 1)[0] if "<actualvolume>" in raw else ""
    return int(value) if value.isdigit() else None


def parse_now_playing(raw: str) -> NowPlaying:
    """Read one /now_playing body into its three fields, applying the documented defaults."""
    return NowPlaying(
        source=raw.split('source="', 1)[1].split('"', 1)[0] if 'source="' in raw else "",
        play_status=(raw.split("<playStatus>", 1)[1].split("</", 1)[0]
                     if "<playStatus>" in raw else "-"),
        item_name=(raw.split("<itemName>", 1)[1].split("</", 1)[0]
                   if "<itemName>" in raw else "-"))


def account_uuid(ip: str) -> str:
    """The speaker's bound account, or "" - the precondition for the SSH injection."""
    return parse_account_uuid(http_get(f"http://{ip}:{API_PORT}/info"))


def migration_verdict(urls: ServiceUrls) -> MigrationVerdict:
    """Is this speaker fully migrated, and if not, what is wrong with it?"""
    return MigrationVerdict(urls)


def wait_down(ip: str, limit: float = 60.0) -> float | None:
    """Prove the speaker actually went down.

    A wait that only checks for "back up" reports success instantly when the reboot never happened,
    which is the case worth catching.
    """
    start = time.monotonic()
    while time.monotonic() - start < limit:
        if not port_open(ip, API_PORT, timeout=2):
            return round(time.monotonic() - start, 1)
        time.sleep(2)
    return None


def wait_up(ip: str, limit: float = 180.0) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < limit:
        if port_open(ip, API_PORT, timeout=2):
            return round(time.monotonic() - start, 1)
        time.sleep(3)
    return None


def wait_port(ip: str, port: int, limit: float) -> float | None:
    """How long until this port accepts, or None inside the limit.

    Polled rather than read once because readiness is per-port: sshd starts after the API port
    answers, so a single immediate reading reports a slow start as a refusal.
    """
    start = time.monotonic()
    while time.monotonic() - start < limit:
        if port_open(ip, port, timeout=2):
            return round(time.monotonic() - start, 1)
        time.sleep(3)
    return None


def _post(ip: str, path: str, body: str) -> None:
    """POST to the speaker; a refusal is a SpeakerError like every failed read, so `main` answers
    it with an envelope instead of a traceback."""
    url = f"http://{ip}:{API_PORT}/{path}"
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15):  # noqa: S310 - fixed http URL built above
            pass
    except OSError as exc:
        raise SpeakerError(f"{url}: {exc}") from exc


def _key(ip: str, name: str) -> None:
    for state in KeyState:
        _post(ip, "key", f'<key state="{state}" sender="Gabbo">{name}</key>')
        time.sleep(0.4)


def _read_now_playing(ip: str) -> NowPlaying:
    return parse_now_playing(http_get(f"http://{ip}:{API_PORT}/now_playing"))


def _volume(ip: str) -> int | None:
    """The speaker's current volume, or None if it could not be read."""
    try:
        return parse_volume(http_get(f"http://{ip}:{API_PORT}/volume"))
    except SpeakerError:
        return None


def _wake(ip: str, limit: float = 20.0) -> None:
    """Take a speaker out of standby before a preset is pressed.

    A PRESET key pressed in standby only WAKES the speaker, onto whatever it played last, so a
    play proof run from standby measures the old station rather than the button it pressed.
    """
    if not _read_now_playing(ip).is_standby:
        return
    _key(ip, Key.POWER)
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and _read_now_playing(ip).is_standby:
        time.sleep(1)
    time.sleep(3)


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--service", help="AfterTouch base URL (needed for migrate)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state")
    p_mig = sub.add_parser("migrate", help="rewrite the four service URLs")
    p_mig.add_argument("--confirm", action="store_true")
    p_reb = sub.add_parser("reboot", help="restart and wait for the radio sources")
    p_reb.add_argument("--confirm", action="store_true")
    p_reb.add_argument("--sources-wait", type=float, default=240.0)
    p_ssh = sub.add_parser("enable-ssh", help="open SSH over the diagnostic port")
    p_ssh.add_argument("--confirm", action="store_true")
    p_ssh.add_argument("--full-config", action="store_true",
                       help="the longer form, plus a reboot: only for a speaker where the default "
                            "form persists but port 22 stays refused")
    p_ssh.add_argument("--assume-paired", action="store_true",
                       help="proceed although /info reports no account. ONLY for firmware that "
                            "reports margeAccountUUID empty WHILE PAIRED (measured on the "
                            "Wireless Link Adapter, 20.0.6). Confirm the speaker really reaches "
                            "the service first - see access-and-rooting.md")
    p_play = sub.add_parser("play", help="play a preset and prove it really played")
    p_play.add_argument("--preset", type=int, default=1)
    p_play.add_argument("--expect", required=True,
                        help="station name that must appear. Required: without it an "
                             "already-playing speaker passes trivially and proves nothing")
    p_play.add_argument("--wait", type=float, default=60.0)
    p_play.add_argument("--confirm", action="store_true",
                        help="required: this starts audio on the speaker, at its current volume")
    return parser


def _emit(command: Command, ok: bool, data: dict[str, object], code: int = 1) -> int:
    """One JSON envelope on every path. 1 is a definite no, 2 is a question left unanswered."""
    print(json.dumps({"ok": ok, "command": command.value, "data": data}, indent=2))
    return 0 if ok else code


def _read_verdict(ip: str) -> MigrationVerdict:
    reply = telnet_run(ip, ["getpdo CurrentSystemConfiguration"])[0]
    return migration_verdict(parse_urls(reply.reply))


def _cmd_state(opts: Options) -> int:
    verdict = _read_verdict(opts.ip)
    sources = parse_sources(http_get(f"http://{opts.ip}:{API_PORT}/sources"))
    return _emit(opts.command, verdict.ok, {**verdict.to_json(), "sources": sources.to_json()})


def _cmd_migrate(opts: Options) -> int:
    if not opts.service:
        return _emit(opts.command, False, {"error": "--service is required for migrate"})
    if not opts.confirm:
        return _emit(opts.command, False,
                     {"would_run": build_url_commands(opts.service),
                      "note": "re-run with --confirm. The speaker must be restarted "
                              "afterwards for the radio sources to mount."})
    replies = telnet_run(opts.ip, build_url_commands(opts.service))
    verdict = _read_verdict(opts.ip)
    truncated = [r.cmd for r in replies if not r.complete]
    return _emit(opts.command, verdict.ok and not truncated, {
        **verdict.to_json(),
        "telnet": [r.to_json() for r in replies],
        "truncated_replies": truncated,
        "next": ("This is the LIVE configuration. Only a reboot shows what was "
                 "persisted, and the radio sources do not mount until then: run "
                 "`reboot --confirm` next, then check `state` again."),
    })


def _cmd_enable_ssh(opts: Options) -> int:
    command = opts.command
    if not opts.service:
        return _emit(command, False, {"error": "--service is required for enable-ssh"})
    if port_open(opts.ip, SSH_PORT):
        return _emit(command, True, {"note": "port 22 is already open; nothing to do"})
    bound = account_uuid(opts.ip)
    if not bound and not opts.assume_paired:
        return _emit(command, False, {"error": "this speaker has no account bound, so it never reads "
                                               "margeServerUrl and the injection would do nothing at "
                                               "all - silently. Bind an account first (see "
                                               "migration.md), then run this again. If this is "
                                               "firmware that reports the field empty WHILE paired "
                                               "(the Wireless Link Adapter on 20.0.6 does), confirm "
                                               "the speaker reaches the service and re-run with "
                                               "--assume-paired."}, code=2)
    commands = build_enable_ssh_commands(opts.service, full_config=opts.full_config)
    if not opts.confirm:
        return _emit(command, False, {"would_run": commands,
                                      "account": bound or "(empty; proceeding on --assume-paired)",
                                      "note": "re-run with --confirm. This writes shell text into a "
                                              "live configuration value, which migrate must clean up "
                                              "afterwards."})
    replies = telnet_run(opts.ip, commands)
    limit = SSH_WAIT_FULL if opts.full_config else SSH_WAIT_STORED
    opened = (wait_up(opts.ip) is not None
              and wait_port(opts.ip, SSH_PORT, limit) is not None)
    # Visible in the envelope on purpose: a run that skipped the precondition must say
    # so, or a later reader cannot tell a bypassed check from a satisfied one.
    bypass: dict[str, object] = (
        {} if bound else {"precondition_bypassed": "margeAccountUUID empty; --assume-paired given"})
    report: dict[str, object] = {"telnet": [r.to_json() for r in replies],
                                 "truncated_replies": [r.cmd for r in replies if not r.complete],
                                 "ssh_open": opened, **bypass}
    if opened:
        return _emit(command, True, {**report,
                                     "next": "Now run `migrate --confirm` to rewrite the four URLs "
                                             "WITHOUT the injected shell text, then persist the "
                                             "marker, or SSH is gone at the next reboot and the "
                                             "account URL keeps shell commands in it."})
    if opts.full_config:
        return _emit(command, False, {**report,
                                      "next": "Port 22 is still refused after the full form's own "
                                              "reboot. Some units never start sshd over telnet at "
                                              "all and need the serial or U-Boot route, which is "
                                              "outside this skill."})
    return _emit(command, False, {**report,
                                  "next": "The injection is STORED but not live yet, which is not a "
                                          "failure. `envswitch boseurls set` writes the stored "
                                          "configuration, and the shell text runs when the speaker "
                                          "reads that value as its RUNTIME margeServerUrl - measured "
                                          "on an ST20 on 27.0.6, at the next boot, and `state` "
                                          "cannot show it before then. Run `reboot --confirm`, then "
                                          "check port 22 again. Escalate to --full-config only if it "
                                          "is still refused after that reboot."}, code=2)


def _wait_for_sources(ip: str, limit: float) -> RadioSources:
    """Poll /sources until internet radio is ready (see RadioSources.radio_ready) or the limit
    passes; the last reading."""
    ready = RadioSources({})
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            ready = parse_sources(http_get(f"http://{ip}:{API_PORT}/sources"))
        except SpeakerError:
            time.sleep(5)
            continue
        if ready.radio_ready():
            break
        time.sleep(5)
    return ready


def _cmd_reboot(opts: Options) -> int:
    command = opts.command
    if not opts.confirm:
        return _emit(command, False, {"note": "re-run with --confirm; the speaker will restart and "
                                              "be unavailable for about 80 seconds"})
    # Measured on two ST20s on 27.0.6: a reboot brought them back at volume 10, not the
    # owner's 41, so the volume is read first and put back once the sources are up.
    volume_before = _volume(opts.ip)
    telnet_run(opts.ip, ["sys reboot"])
    down = wait_down(opts.ip)
    if down is None:
        return _emit(command, False, {"error": "the speaker never went down, so the reboot did not "
                                               "happen"})
    up = wait_up(opts.ip)
    if up is None:
        return _emit(command, False, {"down_after_s": down, "up_after_s": None,
                                      "volume_before": volume_before,
                                      "next": "The speaker did not come back on this address. One whose "
                                              "address comes from plain DHCP can return on another "
                                              "one: run soundtouch_find.py --service <service> to "
                                              "find it, then give it a DHCP reservation. Its volume "
                                              f"was {volume_before} before the reboot."})
    ready = _wait_for_sources(opts.ip, opts.sources_wait)
    volume_after = _volume(opts.ip)
    if volume_before is not None and volume_after != volume_before:
        try:
            _post(opts.ip, "volume", f"<volume>{volume_before}</volume>")
            time.sleep(1)
        except SpeakerError:
            pass
        volume_after = _volume(opts.ip)
    return _emit(command,
                 ready.radio_ready()
                 and (volume_before is None or volume_after == volume_before),
                 {"down_after_s": down, "up_after_s": up, "sources": ready.to_json(),
                  "volume_before": volume_before, "volume_after": volume_after})


def _cmd_play(opts: Options) -> int:
    command = opts.command
    if not opts.confirm:
        return _emit(command, False, {"note": f"re-run with --confirm; this presses {preset_key(opts.preset)}"
                                               " and starts audio at the speaker's current volume. "
                                               "Turn the volume down first."})
    _wake(opts.ip)
    _key(opts.ip, preset_key(opts.preset))
    states: list[str] = []
    item = "-"
    deadline = time.monotonic() + opts.wait
    while time.monotonic() < deadline:
        try:
            reading = _read_now_playing(opts.ip)
        except SpeakerError:
            time.sleep(2)
            continue
        item = reading.item_name
        step = f"{item}/{reading.play_status}"
        if not states or states[-1] != step:
            states.append(step)
        if reading.is_playing and opts.expect.lower() in item.lower():
            break
        time.sleep(2)
    ok = (bool(states) and states[-1].endswith(PlayStatus.PLAY_STATE)
          and opts.expect.lower() in item.lower())
    return _emit(command, ok, {"preset": opts.preset, "expect": opts.expect, "itemName": item,
                               "states": states})


def main(argv: list[str] | None = None) -> int:
    opts = Options.from_namespace(build_parser().parse_args(argv))
    try:
        match opts.command:
            case Command.STATE:
                return _cmd_state(opts)
            case Command.MIGRATE:
                return _cmd_migrate(opts)
            case Command.ENABLE_SSH:
                return _cmd_enable_ssh(opts)
            case Command.REBOOT:
                return _cmd_reboot(opts)
            case Command.PLAY:
                return _cmd_play(opts)
            case _:
                assert_never(opts.command)
    except SpeakerError as exc:
        return _emit(opts.command, False, {"error": str(exc)})


if __name__ == "__main__":
    sys.exit(main())
