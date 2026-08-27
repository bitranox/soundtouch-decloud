#!/usr/bin/env python3
"""Check Docker, render the service compose file, and check the service is alive.

    uv run scripts/soundtouch_service.py check-docker
    uv run scripts/soundtouch_service.py render --host 192.0.2.10 --out docker-compose.yml

Every subcommand prints a JSON envelope: exit 0 yes, 1 no, 2 error.
    uv run scripts/soundtouch_service.py health --service http://192.0.2.10:8000
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import sys

try:
    from soundtouch_core import SpeakerError, http_get
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from soundtouch_core import SpeakerError, http_get

LOOPBACK_HINT = "must be an address the SPEAKERS can reach, never localhost or 127.0.0.1"

_DESKTOP = "Install Docker Desktop from docker.com, start it, then run the check again."
_DEB = ("Run: curl -fsSL https://get.docker.com | sh    then: sudo usermod -aG docker $USER "
        "and log out and back in.")
_RPM = "Run: sudo dnf install docker docker-compose-plugin    then: sudo systemctl enable --now docker"
_NAS = ("Install the Container Manager (Synology) or Container Station (QNAP) package from the "
        "vendor's package centre, then run the check again.")

# Keyed by what an owner actually answers when asked what the machine runs, not by packaging
# family: "ubuntu" and "raspberry pi os" are the two commonest answers, and both used to fall
# through to "which system is this?" while the guide listed them as supported.
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

__all__ = ["build_parser", "install_hint", "render_compose", "validate_host", "docker_report",
           "main", "DEFAULT_MGMT_PASSWORD"]


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
                   *, network: str = "host", mgmt_password: str = DEFAULT_MGMT_PASSWORD) -> str:
    """The compose file, in either networking mode.

    `host` networking is what makes automatic discovery work: it is SSDP and mDNS multicast, which
    Docker's bridge does not forward into a container, so on a bridge the service answers HTTP and
    finds no speakers by itself. It is LINUX ONLY - on Docker Desktop for Windows and macOS it does
    not behave the same way, and the supported route there is published ports plus adding each
    speaker by IP address. Choosing it by platform rather than declaring one mode mandatory is the
    difference between a setup that works and one that looks installed.

    The two modes are mutually exclusive: a `ports:` block alongside `network_mode: host` is
    invalid, Docker only warns, and the leftover block reads as though it applies.
    """
    if network not in ("host", "ports"):
        raise ValueError(f"network must be 'host' or 'ports', got {network!r}")
    ok, why = validate_host(host)
    if not ok:
        raise ValueError(why)
    net = ("    network_mode: host\n" if network == "host"
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
      HTTPS_SERVER_URL: https://{host}:8443
      MGMT_USERNAME: admin
      MGMT_PASSWORD: {mgmt_password}
      RECORD_INTERACTIONS: "true"
      DISCOVERY_INTERVAL: 5m
    volumes:
      - {data_dir}:/app/data
"""


def docker_report() -> dict[str, object]:
    """What is installed, and whether compose is usable."""
    report: dict[str, object] = {"docker": shutil.which("docker") is not None}
    if not report["docker"]:
        report["compose"] = False
        return report
    try:
        proc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, check=False)
        report["compose"] = proc.returncode == 0
        report["compose_version"] = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError) as exc:
        report["compose"] = False
        report["compose_error"] = str(exc)
    return report


def _emit(command: str, ok: bool, data: dict[str, object], code: int = 1) -> int:
    """One JSON envelope on every path, including failure.

    `code` separates the two ways of not being ok: 1 is a definite NO that the walkthrough knows
    how to act on (Docker is not installed yet), 2 is an error that stopped the question being
    answered at all (the service could not be reached, the address was refused).
    """
    print(json.dumps({"ok": ok, "command": command, "data": data}, indent=2))
    return 0 if ok else code


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-docker", help="is Docker and compose available")
    p_render = sub.add_parser("render", help="write the compose file")
    p_render.add_argument("--host", required=True, help="address the speakers will call back to")
    p_render.add_argument("--version", default="latest")
    p_render.add_argument("--data-dir", default="/opt/soundtouch/data",
                          help="host directory holding the service's data")
    p_render.add_argument("--network", choices=("host", "ports"), default="host",
                          help="host networking discovers speakers by itself but is Linux only; "
                               "use ports on Docker Desktop and add speakers by IP")
    p_render.add_argument("--mgmt-password", default=DEFAULT_MGMT_PASSWORD,
                          help="Management API password; the default is published upstream")
    p_render.add_argument("--out", default="",
                          help="write the compose file here; without it the text comes back in "
                               "the JSON envelope")
    p_health = sub.add_parser("health", help="is the service answering")
    p_health.add_argument("--service", required=True)
    p_hint = sub.add_parser("install-hint", help="how to install Docker on one platform")
    p_hint.add_argument("system")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "check-docker":
        rep = docker_report()
        ready = bool(rep.get("docker")) and bool(rep.get("compose"))
        if not ready:
            rep["next"] = "Docker is not usable here. Ask the owner which system this is, then: " \
                          + install_hint("")
        return _emit("check-docker", ready, rep)

    if args.cmd == "install-hint":
        return _emit("install-hint", True, {"system": args.system, "hint": install_hint(args.system)})

    if args.cmd == "render":
        try:
            text = render_compose(args.host, args.version, args.data_dir,
                                  network=args.network, mgmt_password=args.mgmt_password)
        except ValueError as exc:
            return _emit("render", False, {"error": str(exc)}, code=2)
        warnings: list[str] = []
        if args.mgmt_password == DEFAULT_MGMT_PASSWORD:
            warnings.append("MGMT_PASSWORD is the default that upstream publishes in its own "
                            "documentation. Anyone who can reach this machine can drive the "
                            "Management API until it is changed with --mgmt-password.")
        if args.network == "host":
            warnings.append("network_mode: host is Linux only. On Docker Desktop for Windows or "
                            "macOS use --network ports and add each speaker by IP address.")
        data: dict[str, object] = {"host": args.host, "network": args.network,
                                   "compose": text, "warnings": warnings}
        if not args.out:
            return _emit("render", True, data)
        try:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except OSError as exc:
            return _emit("render", False, {"error": str(exc)}, code=2)
        data["path"] = args.out
        return _emit("render", True, data)

    try:
        body = http_get(f"{args.service.rstrip('/')}/api/setup/devices")
    except SpeakerError as exc:
        return _emit("health", False, {"error": str(exc)}, code=2)
    try:
        devices = json.loads(body)
    except json.JSONDecodeError:
        return _emit("health", False, {"error": "the service answered but not with JSON"}, code=2)
    return _emit("health", True, {"devices": len(devices),
                                  "names": [d.get("name") for d in devices if isinstance(d, dict)]})


if __name__ == "__main__":
    sys.exit(main())
