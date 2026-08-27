#!/usr/bin/env python3
"""Report which prerequisites are installed, and how to install the ones that are not.

Run this FIRST, and run it with plain `python3`, not `uv run`:

    python3 scripts/soundtouch_preflight.py
    python3 scripts/soundtouch_preflight.py --system ubuntu

Every other script here is documented as `uv run ...`, which cannot work when `uv` is the thing
that is missing. A checker that needs the tool it is checking for is no checker at all, so this one
imports nothing outside the standard library and runs on whatever Python the owner already has.

Exit 0 when everything REQUIRED is present, 1 when something required is missing, 2 on an error.
`pytest` is reported but never required: it is for people changing the skill, not using it.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys

try:
    from soundtouch_service import install_hint
except ModuleNotFoundError:  # pragma: no cover - direct execution from another directory
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from soundtouch_service import install_hint

MIN_PYTHON = (3, 11)

_UV_POSIX = ("curl -LsSf https://astral.sh/uv/install.sh | sh    "
             "(or `brew install uv`, or `pipx install uv`), then open a new terminal")
_UV_WINDOWS = ('powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
               "    (or `winget install astral-sh.uv`), then open a new terminal")

_COMPOSE_DESKTOP = ("Docker Desktop ships compose. If `docker compose version` fails, update "
                    "Docker Desktop and make sure it is running")
_COMPOSE_DEFAULT = "Install your platform's `docker-compose-plugin` package"
_COMPOSE_HINTS = {
    "windows": _COMPOSE_DESKTOP,
    "macos": _COMPOSE_DESKTOP,
    "debian": "`sudo apt install docker-compose-plugin`",
    "fedora": "`sudo dnf install docker-compose-plugin`",
    "nas": "Reinstall or update the Container Manager / Container Station package, which includes it",
}

_PY_HINTS = {
    "windows": "Install Python from python.org and tick 'Add python.exe to PATH' in the installer",
    "macos": "`brew install python`, or download the installer from python.org",
    "debian": "`sudo apt install python3`",
    "fedora": "`sudo dnf install python3`",
    "nas": "Install the Python package from the vendor's package centre",
}

__all__ = ["detect_system", "check_python", "check_uv", "check_docker", "check_compose",
           "check_pytest", "run_checks", "build_parser", "main"]


def detect_system(system: str = "", *, release: str = "/etc/os-release") -> str:
    """The platform key the hint tables are written against.

    Detected rather than asked, because an owner who cannot tell you whether their box is Debian or
    Fedora is exactly the person this skill is for. An explicit --system still wins, for the cases
    detection cannot see: a container, a NAS with a Linux userland, someone checking for a machine
    that is not the one in front of them.
    """
    if system:
        return system.strip().lower()
    name = platform.system().lower()
    if name == "windows":
        return "windows"
    if name == "darwin":
        return "macos"
    return _linux_family(release)


def _linux_family(release: str = "/etc/os-release") -> str:
    """Which family of Linux, read from os-release rather than guessed from the kernel."""
    try:
        with open(release, encoding="utf-8") as handle:
            fields = dict(line.rstrip("\n").split("=", 1) for line in handle if "=" in line)
    except OSError:
        return "linux"
    ident = fields.get("ID", "").strip('"').lower()
    like = fields.get("ID_LIKE", "").strip('"').lower().split()
    for candidate in [ident, *like]:
        if candidate in ("debian", "ubuntu", "raspbian"):
            return "debian"
        if candidate in ("fedora", "rhel", "centos"):
            return "fedora"
    return ident or "linux"


def _version_of(argv: list[str]) -> str:
    """The first line a tool prints for its version, or "" if it cannot be run at all."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or done.stderr or "").strip().splitlines()[0] if done.returncode == 0 else ""


def check_python() -> dict[str, object]:
    """The interpreter running this file, which is the one the owner would use."""
    current = sys.version_info[:2]
    ok = current >= MIN_PYTHON
    return {"tool": "python", "required": True, "present": ok,
            "detail": platform.python_version(),
            "why": "runs every script in this skill"}


def check_uv(system: str, *, which=shutil.which, version=None) -> dict[str, object]:
    """uv, which every other documented command starts with."""
    version = version or _version_of
    found = which("uv")
    return {"tool": "uv", "required": True, "present": bool(found),
            "detail": version(["uv", "--version"]) if found else "not on PATH",
            "why": "every command in this skill is written as `uv run ...`",
            "install": _UV_WINDOWS if system == "windows" else _UV_POSIX}


def check_docker(system: str, *, which=shutil.which, version=None) -> dict[str, object]:
    """The Docker engine itself, and nothing else."""
    version = version or _version_of
    found = which("docker")
    return {"tool": "docker", "required": True, "present": bool(found),
            "detail": version(["docker", "--version"]) if found else "not on PATH",
            "why": "the replacement service runs as a container",
            "install": install_hint(system)}


def check_compose(system: str, *, which=shutil.which, version=None) -> dict[str, object]:
    """The compose plugin, reported on its own line rather than folded into Docker.

    `docker` on PATH without `docker compose` is a real and common state, and it fails later at
    `docker compose up`. Folding the two together got the VERDICT right and the ADVICE wrong: it
    told somebody who had just installed Docker to install Docker. The plugin is its own package on
    most Linux distributions, so it gets its own instruction.
    """
    version = version or _version_of
    detail = version(["docker", "compose", "version"]) if which("docker") else ""
    # Named for the COMMAND, not the package. `docker-compose` is also the deprecated standalone
    # v1 binary, so a reader who searches that label lands on the wrong tool and can install it.
    return {"tool": "docker compose", "required": True, "present": bool(detail),
            "detail": detail or "not available",
            "why": "the service is started with `docker compose up`",
            "install": _COMPOSE_HINTS.get(system, _COMPOSE_DEFAULT)}


def check_pytest(*, which=shutil.which, version=None) -> dict[str, object]:
    """Only needed by somebody CHANGING the skill, so it is reported and never required."""
    version = version or _version_of
    found = which("pytest")
    return {"tool": "pytest", "required": False, "present": bool(found),
            "detail": version(["pytest", "--version"]) if found else "not on PATH",
            "why": "only for running this skill's own tests",
            "install": "`uv run --with pytest pytest`, which needs no separate install"}


def run_checks(system: str, *, which=shutil.which, version=None) -> list[dict[str, object]]:
    """Every check, in the order the owner needs them.

    BOTH seams are forwarded. Injecting only the PATH lookup leaves the version calls hitting the
    real machine, so a test that says "pretend docker is installed" still asks the actual docker
    for its version - which passes wherever docker happens to exist and fails everywhere else.
    """
    results = [check_python(), check_uv(system, which=which, version=version),
               check_docker(system, which=which, version=version),
               check_compose(system, which=which, version=version),
               check_pytest(which=which, version=version)]
    for result in results:
        if result["tool"] == "python" and not result["present"]:
            # uv can install a Python itself, so the floor is a smaller problem than it looks.
            result["install"] = (_PY_HINTS.get(system, "Install Python 3.11 or newer")
                                 + ". With uv already installed, `uv python install 3.13` does it")
    return results


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", default="",
                        help="override the detected platform (windows, macos, debian, fedora, ...)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        system = detect_system(args.system)
        results = run_checks(system)
    except Exception as exc:  # noqa: BLE001 - a preflight reports, it never crashes the walkthrough
        print(json.dumps({"ok": False, "command": "preflight", "data": {"error": str(exc)}}, indent=2))
        return 2
    missing = [r for r in results if r["required"] and not r["present"]]
    print(json.dumps({"ok": not missing, "command": "preflight",
                      "data": {"system": system, "missing": [r["tool"] for r in missing],
                               "checks": results}}, indent=2))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
