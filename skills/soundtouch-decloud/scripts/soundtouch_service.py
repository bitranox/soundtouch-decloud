#!/usr/bin/env python3
"""Check Docker, render the service compose file, and check the service is alive.

    uv run scripts/soundtouch_service.py check-docker
    uv run scripts/soundtouch_service.py install-hint ubuntu
    uv run scripts/soundtouch_service.py render --host 192.0.2.10 --out docker-compose.yml
    uv run scripts/soundtouch_service.py health --service http://192.0.2.10:8000

`health` also reads the BMX registry the speakers are sent to and says no when it names another
address. Every subcommand prints a JSON envelope: exit 0 yes, 1 no, 2 error.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

try:
    from soundtouch_core import (
        RegistryVerdict,
        SpeakerError,
        http_get,
        registry_verdict,
    )
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from soundtouch_core import (
        RegistryVerdict,
        SpeakerError,
        http_get,
        registry_verdict,
    )

LOOPBACK_HINT = "must be an address the SPEAKERS can reach, never localhost or 127.0.0.1"

_DESKTOP = "Install Docker Desktop from docker.com, start it, then run the check again."
_DEB = ("Run: curl -fsSL https://get.docker.com | sh    then: sudo usermod -aG docker $USER "
        "and log out and back in.")
_RPM = "Run: sudo dnf install docker docker-compose-plugin    then: sudo systemctl enable --now docker"
_NAS = ("Install the Container Manager (Synology) or Container Station (QNAP) package from the "
        "vendor's package centre, then run the check again.")

# Keyed by what an owner actually answers when asked what the machine runs, not by packaging
# family: "ubuntu" and "raspberry pi os" are the two commonest answers, so each is a key of its own.
INSTALL_HINTS = {
    "windows": _DESKTOP,
    "macos": _DESKTOP,
    "mac": _DESKTOP,
    "debian": _DEB,
    "ubuntu": _DEB,
    "raspberry pi os": _DEB,
    "raspbian": _DEB,
    "linux mint": _DEB,
    "pop os": _DEB,
    "fedora": _RPM,
    "rhel": _RPM,
    "centos": _RPM,
    "rocky": _RPM,
    "almalinux": _RPM,
    "synology": _NAS,
    "qnap": _NAS,
    "nas": _NAS,
}

__all__ = ["Command", "DockerReport", "Network", "build_parser", "install_hint", "render_compose",
           "validate_host", "docker_report", "main", "DEFAULT_MGMT_PASSWORD"]


class Command(StrEnum):
    """The subcommands, spelled once: the argparse names and the envelope's "command" field."""

    CHECK_DOCKER = "check-docker"
    INSTALL_HINT = "install-hint"
    RENDER = "render"
    HEALTH = "health"


class Network(StrEnum):
    """The two compose networking modes; they exclude each other in the rendered file."""

    HOST = "host"
    PORTS = "ports"


@dataclass(frozen=True)
class DockerReport:
    """What `docker compose version` told us, with only the fields that path could fill.

    `compose_version` is set when the command ran, `compose_error` when it could not be run at all;
    both stay None when Docker itself is missing, so the JSON carries exactly the keys that apply.
    """

    docker: bool
    compose: bool
    compose_version: str | None = None
    compose_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.docker and self.compose

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"docker": self.docker, "compose": self.compose}
        if self.compose_version is not None:
            out["compose_version"] = self.compose_version
        if self.compose_error is not None:
            out["compose_error"] = self.compose_error
        return out


@dataclass(frozen=True)
class DeviceListing:
    """The service's device list: how many entries it held, and the names of the object entries.

    `count` includes entries that are not objects, so it is the length of the list the service
    sent; a device without a name contributes None to `names`.
    """

    count: int
    names: tuple[object, ...]

    def to_json(self) -> dict[str, object]:
        return {"devices": self.count, "names": list(self.names)}


def parse_devices(payload: object) -> DeviceListing | None:
    """The device listing in a decoded JSON payload, or None when it is not a list."""
    if not isinstance(payload, list):
        return None
    entries = cast("list[object]", payload)
    names = tuple(cast("dict[str, object]", e).get("name") for e in entries if isinstance(e, dict))
    return DeviceListing(count=len(entries), names=names)


def install_hint(system: str) -> str:
    """The instruction for one platform, or a prompt to name the platform."""
    return INSTALL_HINTS.get(system.strip().lower(),
                             "Ask which system this machine runs: "
                             + ", ".join(sorted(INSTALL_HINTS)))


def validate_host(host: str) -> tuple[bool, str]:
    """Reject an address the speakers could never call back to.

    A loopback address here is the quiet killer: the service starts, the owner can browse it, and
    every speaker is told to call itself.
    """
    if not host or host != host.strip():
        return False, "empty or padded address"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if host in ("localhost", "localhost.localdomain"):
            return False, f"'{host}' {LOOPBACK_HINT}"
        return True, "hostname (make sure it resolves to the LAN address on every speaker)"
    if addr.is_loopback:
        return False, f"'{host}' {LOOPBACK_HINT}"
    if addr.is_unspecified or addr.is_multicast:
        return False, f"'{host}' is not a usable host address"
    return True, "ok"


DEFAULT_MGMT_PASSWORD = "change_me!"  # what upstream ships, and publishes in its own docs


def render_compose(host: str, version: str = "latest", data_dir: str = "/opt/soundtouch/data",
                   *, network: Network = Network.HOST, mgmt_password: str = DEFAULT_MGMT_PASSWORD) -> str:
    """The compose file, in either networking mode.

    `host` networking is what makes automatic discovery work: it is SSDP and mDNS multicast, which
    Docker's bridge does not forward into a container, so on a bridge the service answers HTTP and
    finds no speakers by itself. It is LINUX ONLY - on Docker Desktop for Windows and macOS it does
    not behave the same way, and the supported route there is published ports plus adding each
    speaker by IP address. Choosing it by platform rather than declaring one mode mandatory is the
    difference between a setup that works and one that looks installed.

    The two modes are mutually exclusive: a `ports:` block alongside `network_mode: host` is
    invalid, Docker only warns, and the leftover block reads as though it applies.

    HTTPS_SERVER_URL is left out on purpose: the service derives it from SERVER_URL (same host,
    https, HTTPS_PORT), so setting it only adds a second copy of the address to keep in step.
    """
    try:
        mode = Network(network)
    except ValueError:
        # A caller outside the CLI may still hand over a bare string; refuse it by name.
        raise ValueError(f"network must be 'host' or 'ports', got {network!r}") from None
    ok, why = validate_host(host)
    if not ok:
        raise ValueError(why)
    net = ("    network_mode: host\n" if mode is Network.HOST
           else '    ports:\n      - "8000:8000"\n      - "8443:8443"\n')
    return f"""services:
  soundtouch-service:
    image: ghcr.io/gesellix/bose-soundtouch:{version}
    container_name: soundtouch-service
    restart: unless-stopped
{net}    environment:
      PORT: 8000
      HTTPS_PORT: 8443
      DATA_DIR: /app/data
      SERVER_URL: http://{host}:8000
      MGMT_USERNAME: admin
      MGMT_PASSWORD: {mgmt_password}
      RECORD_INTERACTIONS: "true"
      DISCOVERY_INTERVAL: 5m
    volumes:
      - {data_dir}:/app/data
"""


def docker_report() -> DockerReport:
    """What is installed, and whether compose is usable."""
    if shutil.which("docker") is None:
        return DockerReport(docker=False, compose=False)
    try:
        proc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return DockerReport(docker=True, compose=False, compose_error=str(exc))
    version = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    return DockerReport(docker=True, compose=proc.returncode == 0, compose_version=version)


def _emit(command: Command, ok: bool, data: dict[str, object], code: int = 1) -> int:
    """One JSON envelope on every path, including failure.

    `code` separates the two ways of not being ok: 1 is a definite NO that the walkthrough knows
    how to act on (Docker is not installed yet), 2 is an error that stopped the question being
    answered at all (the service could not be reached, the address was refused).
    """
    print(json.dumps({"ok": ok, "command": command.value, "data": data}, indent=2))
    return 0 if ok else code


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser(Command.CHECK_DOCKER.value, help="is Docker and compose available")
    p_render = sub.add_parser(Command.RENDER.value, help="write the compose file")
    p_render.add_argument("--host", required=True, help="address the speakers will call back to")
    p_render.add_argument("--version", default="latest")
    p_render.add_argument("--data-dir", default="/opt/soundtouch/data",
                          help="host directory holding the service's data")
    # Plain strings, not members, so argparse's own usage and error text stays the bare values.
    p_render.add_argument("--network", choices=[n.value for n in Network],
                          default=Network.HOST.value,
                          help="host networking discovers speakers by itself but is Linux only; "
                               "use ports on Docker Desktop and add speakers by IP")
    p_render.add_argument("--mgmt-password", default=DEFAULT_MGMT_PASSWORD,
                          help="Management API password; the default is published upstream")
    p_render.add_argument("--out", default="",
                          help="write the compose file here; without it the text comes back in "
                               "the JSON envelope")
    p_health = sub.add_parser(Command.HEALTH.value, help="is the service answering")
    p_health.add_argument("--service", required=True)
    p_hint = sub.add_parser(Command.INSTALL_HINT.value, help="how to install Docker on one platform")
    p_hint.add_argument("system")
    return parser


_FOREIGN_REGISTRY_NEXT = (
    "The service sends every speaker to another address for its radio. Its "
    "persisted settings.json server_url beats the SERVER_URL it was started "
    "with, which is what a copied install carries over. Set server_url and "
    "https_server_url there (or on the Settings page) to this service's own "
    "address and restart it. Then reboot every speaker "
    "(soundtouch_onboard.py reboot --confirm): a speaker reads the registry "
    "when it starts, so until it restarts it keeps the old address, and this "
    "check, which reads the SERVICE, already says ok.")


def _check_docker() -> int:
    report = docker_report()
    data = report.to_json()
    if not report.ready:
        data["next"] = ("Docker is not usable here. Ask the owner which system this is, then: "
                        + install_hint(""))
    return _emit(Command.CHECK_DOCKER, report.ready, data)


def _render(args: argparse.Namespace) -> int:
    network = Network(args.network)
    try:
        text = render_compose(args.host, args.version, args.data_dir,
                              network=network, mgmt_password=args.mgmt_password)
    except ValueError as exc:
        return _emit(Command.RENDER, False, {"error": str(exc)}, code=2)
    warnings: list[str] = []
    if args.mgmt_password == DEFAULT_MGMT_PASSWORD:
        warnings.append("MGMT_PASSWORD is the default that upstream publishes in its own "
                        "documentation. Anyone who can reach this machine can drive the "
                        "Management API until it is changed with --mgmt-password.")
    if network is Network.HOST:
        warnings.append("network_mode: host is Linux only. On Docker Desktop for Windows or "
                        "macOS use --network ports and add each speaker by IP address.")
    data: dict[str, object] = {"host": args.host, "network": network.value,
                               "compose": text, "warnings": warnings}
    if not args.out:
        return _emit(Command.RENDER, True, data)
    try:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        return _emit(Command.RENDER, False, {"error": str(exc)}, code=2)
    data["path"] = args.out
    return _emit(Command.RENDER, True, data)


def _health(args: argparse.Namespace) -> int:
    service: str = args.service
    try:
        body = http_get(f"{service.rstrip('/')}/api/setup/devices")
    except SpeakerError as exc:
        return _emit(Command.HEALTH, False, {"error": str(exc)}, code=2)
    try:
        listing = parse_devices(json.loads(body))
    except json.JSONDecodeError:
        listing = None
    if listing is None:
        return _emit(Command.HEALTH, False, {"error": "the service answered but not with JSON"},
                     code=2)
    data = listing.to_json()
    try:
        registry = registry_verdict(service, http_get(
            f"{service.rstrip('/')}/bmx/registry/v1/services"))
    except SpeakerError as exc:
        return _emit(Command.HEALTH, False, {**data, "error": f"registry: {exc}"}, code=2)
    data["registry"] = registry.to_json()
    if registry.verdict is RegistryVerdict.UNREADABLE:
        return _emit(Command.HEALTH, False, {**data, "error": "the registry did not name TUNEIN and "
                                                              "LOCAL_INTERNET_RADIO"}, code=2)
    if registry.verdict is RegistryVerdict.FOREIGN:
        data["next"] = _FOREIGN_REGISTRY_NEXT
        return _emit(Command.HEALTH, False, data)
    return _emit(Command.HEALTH, True, data)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = Command(args.cmd)
    match command:
        case Command.CHECK_DOCKER:
            return _check_docker()
        case Command.INSTALL_HINT:
            return _emit(command, True, {"system": args.system, "hint": install_hint(args.system)})
        case Command.RENDER:
            return _render(args)
        case Command.HEALTH:
            return _health(args)


if __name__ == "__main__":
    sys.exit(main())
