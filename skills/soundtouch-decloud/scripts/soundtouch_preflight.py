#!/usr/bin/env python3
"""Report which prerequisites are installed, and how to install the ones that are not.

Run this FIRST, and run it with plain `python3`, not `uv run`:

    python3 scripts/soundtouch_preflight.py
    python3 scripts/soundtouch_preflight.py --system ubuntu

Every other script here is documented as `uv run ...`, which cannot work when `uv` is the thing
that is missing. A checker that needs the tool it is checking for is no checker at all, so this one
imports nothing outside the standard library and still starts on a Python older than the 3.11 the
other scripts need, so that it can say so.

Exit 0 when everything REQUIRED is present, 1 when something required is missing, 2 on an error.
`pytest` is reported but never required: it is for people changing the skill, not using it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

# This script must START on the old Python it exists to report, so the 3.11-only pieces are
# gated and the runtime type aliases below are strings.
if sys.version_info >= (3, 11):  # noqa: UP036 - this script's floor is 3.9, below the others'
    from enum import StrEnum
else:  # pragma: no cover - the old Python this script exists to report
    class StrEnum(str, Enum):
        """The members ARE their values, as enum.StrEnum makes them on 3.11."""

        def __str__(self) -> str:
            return str(self.value)


def _upgrade_python_first(_system: str) -> str:
    return ("Upgrade Python to 3.11 or newer first: the Docker instruction comes from a script "
            "that needs it. Then run this check again.")


def _service_install_hint() -> Callable[[str], str]:
    """soundtouch_service's Docker instruction, or a stand-in on a Python too old to import it."""
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        # Local, because soundtouch_service needs Python 3.11 and this script must start without it.
        from soundtouch_service import install_hint
    except (ImportError, SyntaxError):
        return _upgrade_python_first
    return install_hint


install_hint = _service_install_hint()

MIN_PYTHON = (3, 11)

WhichFn = Callable[[str], "str | None"]
VersionFn = Callable[["list[str]"], str]


class Tool(StrEnum):
    """The prerequisites this script reports on, spelled as the owner sees them."""

    PYTHON = "python"
    UV = "uv"
    DOCKER = "docker"
    COMPOSE = "docker compose"
    PYTEST = "pytest"


class SystemFamily(StrEnum):
    """The platform families detection produces and the hint tables are keyed by.

    NAS never comes out of detection; it is reachable only through an explicit --system. Any other
    answer (an unknown os-release ID, a free-text --system) stays a plain str and simply finds no
    entry in the tables.
    """

    WINDOWS = "windows"
    MACOS = "macos"
    DEBIAN = "debian"
    FEDORA = "fedora"
    LINUX = "linux"
    NAS = "nas"


@dataclass(frozen=True)
class CheckResult:
    """One prerequisite: whether it is there, what it reported, and how to get it if not."""

    tool: Tool
    required: bool
    present: bool
    detail: str
    why: str
    install: str | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"tool": self.tool.value, "required": self.required,
                                  "present": self.present, "detail": self.detail, "why": self.why}
        if self.install is not None:
            out["install"] = self.install
        return out

# What an owner answers, or os-release names, mapped to the family the hint tables are keyed by.
# Every answer soundtouch_service's Docker table knows must appear here (a test holds the two
# lists together), or an owner who typed it gets generic compose and Python advice.
_FAMILY_OF: dict[str, SystemFamily] = {
    "windows": SystemFamily.WINDOWS,
    "macos": SystemFamily.MACOS, "mac": SystemFamily.MACOS,
    "debian": SystemFamily.DEBIAN, "ubuntu": SystemFamily.DEBIAN,
    "raspberry pi os": SystemFamily.DEBIAN, "raspbian": SystemFamily.DEBIAN,
    "linux mint": SystemFamily.DEBIAN, "pop os": SystemFamily.DEBIAN,
    "fedora": SystemFamily.FEDORA, "rhel": SystemFamily.FEDORA, "centos": SystemFamily.FEDORA,
    "rocky": SystemFamily.FEDORA, "almalinux": SystemFamily.FEDORA,
    "synology": SystemFamily.NAS, "qnap": SystemFamily.NAS, "nas": SystemFamily.NAS,
    "linux": SystemFamily.LINUX,
}

_UV_POSIX = ("curl -LsSf https://astral.sh/uv/install.sh | sh    "
             "(or `brew install uv`, or `pipx install uv`), then open a new terminal")
_UV_WINDOWS = ('powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
               "    (or `winget install astral-sh.uv`), then open a new terminal")

_COMPOSE_DESKTOP = ("Docker Desktop ships compose. If `docker compose version` fails, update "
                    "Docker Desktop and make sure it is running")
_COMPOSE_DEFAULT = "Install your platform's `docker-compose-plugin` package"
_COMPOSE_HINTS: dict[str, str] = {
    SystemFamily.WINDOWS: _COMPOSE_DESKTOP,
    SystemFamily.MACOS: _COMPOSE_DESKTOP,
    SystemFamily.DEBIAN: "`sudo apt install docker-compose-plugin`",
    SystemFamily.FEDORA: "`sudo dnf install docker-compose-plugin`",
    SystemFamily.NAS: "Reinstall or update the Container Manager / Container Station package, which includes it",
}

_PY_HINTS: dict[str, str] = {
    SystemFamily.WINDOWS: "Install Python from python.org and tick 'Add python.exe to PATH' in the installer",
    SystemFamily.MACOS: "`brew install python`, or download the installer from python.org",
    SystemFamily.DEBIAN: "`sudo apt install python3`",
    SystemFamily.FEDORA: "`sudo dnf install python3`",
    SystemFamily.NAS: "Install the Python package from the vendor's package centre",
}

__all__ = ["CheckResult", "SystemFamily", "Tool", "detect_system", "check_python", "check_uv", "check_docker", "check_compose",
           "check_pytest", "system_family", "run_checks", "build_parser", "main"]


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
        return SystemFamily.WINDOWS
    if name == "darwin":
        return SystemFamily.MACOS
    return _linux_family(release)


def _linux_family(release: str = "/etc/os-release") -> str:
    """Which family of Linux, read from os-release rather than guessed from the kernel."""
    try:
        with open(release, encoding="utf-8") as handle:
            fields = dict(line.rstrip("\n").split("=", 1) for line in handle if "=" in line)
    except OSError:
        return SystemFamily.LINUX
    ident = fields.get("ID", "").strip('"').lower()
    like = fields.get("ID_LIKE", "").strip('"').lower().split()
    for candidate in [ident, *like]:
        if _FAMILY_OF.get(candidate) in (SystemFamily.DEBIAN, SystemFamily.FEDORA):
            return _FAMILY_OF[candidate]
    return ident or SystemFamily.LINUX


def system_family(system: str) -> str:
    """The family whose hints apply to an owner's answer or a detected key ("ubuntu" -> debian).

    An answer no table knows passes through unchanged and finds the generic hints.
    """
    key = system.strip().lower()
    return _FAMILY_OF.get(key, key)


def _version_of(argv: list[str]) -> str:
    """The first line a tool prints for its version, or "" if it cannot be run at all."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or done.stderr or "").strip().splitlines()[0] if done.returncode == 0 else ""


def check_python() -> CheckResult:
    """The interpreter running this file, which is the one the owner would use."""
    current = sys.version_info[:2]
    return CheckResult(tool=Tool.PYTHON, required=True, present=current >= MIN_PYTHON,
                       detail=platform.python_version(), why="runs every script in this skill")


def check_uv(system: str, *, which: WhichFn = shutil.which,
             version: VersionFn = _version_of) -> CheckResult:
    """uv, which every other documented command starts with."""
    found = which("uv")
    return CheckResult(
        tool=Tool.UV, required=True, present=bool(found),
        detail=version(["uv", "--version"]) if found else "not on PATH",
        why="every command in this skill is written as `uv run ...`",
        install=_UV_WINDOWS if system == SystemFamily.WINDOWS else _UV_POSIX)


def check_docker(system: str, *, which: WhichFn = shutil.which,
                 version: VersionFn = _version_of) -> CheckResult:
    """The Docker engine itself, and nothing else."""
    found = which("docker")
    return CheckResult(
        tool=Tool.DOCKER, required=True, present=bool(found),
        detail=version(["docker", "--version"]) if found else "not on PATH",
        why="the replacement service runs as a container", install=install_hint(system))


def check_compose(system: str, *, which: WhichFn = shutil.which,
                  version: VersionFn = _version_of) -> CheckResult:
    """The compose plugin, reported on its own line rather than folded into Docker.

    `docker` on PATH without `docker compose` is a real and common state, and it fails later at
    `docker compose up`. Folded into one check, the VERDICT would be right and the ADVICE wrong: it
    would tell somebody who has just installed Docker to install Docker. The plugin is its own package on
    most Linux distributions, so it gets its own instruction.
    """
    detail = version(["docker", "compose", "version"]) if which("docker") else ""
    # Named for the COMMAND, not the package. `docker-compose` is also the deprecated standalone
    # v1 binary, so a reader who searches that label lands on the wrong tool and can install it.
    return CheckResult(
        tool=Tool.COMPOSE, required=True, present=bool(detail), detail=detail or "not available",
        why="the service is started with `docker compose up`",
        install=_COMPOSE_HINTS.get(system_family(system), _COMPOSE_DEFAULT))


def check_pytest(*, which: WhichFn = shutil.which, version: VersionFn = _version_of) -> CheckResult:
    """Only needed by somebody CHANGING the skill, so it is reported and never required."""
    found = which("pytest")
    return CheckResult(
        tool=Tool.PYTEST, required=False, present=bool(found),
        detail=version(["pytest", "--version"]) if found else "not on PATH",
        why="only for running this skill's own tests",
        install="`uv run --with pytest pytest`, which needs no separate install")


def _with_python_install(result: CheckResult, system: str) -> CheckResult:
    """A missing Python gets an instruction; uv can install one itself, so the floor is a smaller
    problem than it looks."""
    if result.tool is not Tool.PYTHON or result.present:
        return result
    hint = _PY_HINTS.get(system_family(system), "Install Python 3.11 or newer")
    return replace(result, install=hint + ". With uv already installed, `uv python install 3.13` does it")


def run_checks(system: str, *, which: WhichFn = shutil.which,
               version: VersionFn = _version_of) -> list[CheckResult]:
    """Every check, in the order the owner needs them.

    BOTH seams are forwarded. Injecting only the PATH lookup leaves the version calls hitting the
    real machine, so a test that says "pretend docker is installed" still asks the actual docker
    for its version - which passes wherever docker happens to exist and fails everywhere else.
    """
    results = [check_python(), check_uv(system, which=which, version=version),
               check_docker(system, which=which, version=version),
               check_compose(system, which=which, version=version),
               check_pytest(which=which, version=version)]
    return [_with_python_install(result, system) for result in results]


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
    missing = [r for r in results if r.required and not r.present]
    print(json.dumps({"ok": not missing, "command": "preflight",
                      "data": {"system": system, "missing": [r.tool.value for r in missing],
                               "checks": [r.to_json() for r in results]}}, indent=2))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
