#!/usr/bin/env python3
"""Check the conventions this repo cannot express as a test of the skill itself.

The skill's own suite proves the scripts behave. This proves the REPO still holds together: that
the plugin manifests agree with the directory they describe, that the skill's frontmatter is the
shape the router needs, that every shipped script has tests, and that nothing arrived with CRLF or
a typographic character the house style bans.

Every check returns a list of failures rather than raising, so one run reports everything wrong at
once instead of stopping at the first thing.

    python scripts/check_repo.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
DESCRIPTION_MAX = 1024
# Built from CODE POINTS, never from the characters themselves: a literal list here would make
# this file fail its own check, which is exactly what happened the first time it ran.
TELLS = frozenset(chr(c) for c in (0x2014, 0x2013, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026,
                                   0x00A0, 0x200B, 0xFEFF))
TEXT_SUFFIXES = (".md", ".py", ".json", ".yml", ".yaml", ".toml", ".txt", ".gitignore")

__all__ = ["check_manifests", "check_skill", "check_tests_exist", "check_line_endings",
           "check_no_typographic_tells", "run_checks", "main"]


def _load_json(path: pathlib.Path, root: pathlib.Path) -> tuple[dict | None, list[str]]:
    """Parse a manifest, reporting a bad one as a failure rather than a traceback.

    The root is passed rather than read from the module constant so the message names a path under
    the tree actually being checked; using the constant raised ValueError on any other tree, which
    turned a reportable failure into a crash.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{path.relative_to(root)}: {exc}"]


def check_manifests(root: pathlib.Path) -> list[str]:
    """The two manifests must agree with each other and with the directory they ship."""
    plugin, fails = _load_json(root / ".claude-plugin" / "plugin.json", root)
    market, more = _load_json(root / ".claude-plugin" / "marketplace.json", root)
    fails += more
    if plugin is None or market is None:
        return fails
    if not SEMVER.match(str(plugin.get("version", ""))):
        fails.append(f"plugin.json version {plugin.get('version')!r} is not X.Y.Z")
    listed = [p.get("name") for p in market.get("plugins", [])]
    if listed != [plugin.get("name")]:
        fails.append(f"marketplace.json lists {listed}, plugin.json is {plugin.get('name')!r}")
    if plugin.get("name") != root.name:
        fails.append(f"plugin.json name {plugin.get('name')!r} is not the repo dir {root.name!r}")
    return fails


def _frontmatter(text: str) -> dict[str, str]:
    """The YAML-ish frontmatter as a flat mapping. Only `name` and `description` are ever used."""
    if not text.startswith("---\n"):
        return {}
    body = text.split("---\n", 2)[1]
    return {k.strip(): v.strip()
            for k, _, v in (line.partition(":") for line in body.splitlines()) if k.strip()}


def check_skill(root: pathlib.Path) -> list[str]:
    """The skill's frontmatter has to be what the router reads, and match its own directory."""
    skills = sorted((root / "skills").glob("*/SKILL.md"))
    if len(skills) != 1:
        return [f"expected exactly one skills/*/SKILL.md, found {len(skills)}"]
    skill = skills[0]
    front = _frontmatter(skill.read_text(encoding="utf-8"))
    fails = []
    if front.get("name") != skill.parent.name:
        fails.append(f"SKILL.md name {front.get('name')!r} is not its dir {skill.parent.name!r}")
    description = front.get("description", "")
    if not description.startswith("Use when"):
        fails.append("SKILL.md description must start with 'Use when' so the router can match it")
    if len(description) > DESCRIPTION_MAX:
        # Over the cap is TRUNCATED silently, not refused, so every trigger past the cut simply
        # stops reaching the router while the file still reads perfectly.
        fails.append(f"SKILL.md description is {len(description)} chars, over {DESCRIPTION_MAX}")
    return fails


def check_tests_exist(root: pathlib.Path) -> list[str]:
    """Every shipped script needs a test that actually names it."""
    fails = []
    for skill in sorted((root / "skills").glob("*/SKILL.md")):
        tests = list(skill.parent.glob("tests/test_*.py"))
        corpus = "\n".join(t.read_text(encoding="utf-8") for t in tests)
        for script in sorted(skill.parent.glob("scripts/*.py")):
            if script.stem not in corpus:
                fails.append(f"{script.relative_to(root)} is named by no test")
    return fails


def _tracked_text_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Text files worth checking, skipping the places generated junk lives."""
    skip = {".git", "__pycache__", ".venv"}
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in TEXT_SUFFIXES and not skip & set(p.parts)]


def check_line_endings(root: pathlib.Path) -> list[str]:
    """A CRLF here makes a shipped script unrunnable on the machine that installs it."""
    return [f"{p.relative_to(root)} contains CRLF"
            for p in _tracked_text_files(root) if b"\r\n" in p.read_bytes()]


def check_no_typographic_tells(root: pathlib.Path) -> list[str]:
    """House style is ASCII: no em-dash, curly quote, ellipsis, non-breaking space or BOM."""
    fails = []
    for path in _tracked_text_files(root):
        found = sorted({c for c in path.read_text(encoding="utf-8") if c in TELLS})
        if found:
            names = ", ".join(f"U+{ord(c):04X}" for c in found)
            fails.append(f"{path.relative_to(root)} contains {names}")
    return fails


def run_checks(root: pathlib.Path) -> list[str]:
    """Every check, so one run reports everything rather than the first thing."""
    fails: list[str] = []
    for check in (check_manifests, check_skill, check_tests_exist, check_line_endings,
                  check_no_typographic_tells):
        fails += check(root)
    return fails


def main(argv: list[str] | None = None) -> int:
    root = pathlib.Path(argv[0]).resolve() if argv else ROOT
    fails = run_checks(root)
    for failure in fails:
        print(f"  FAIL {failure}")
    print(f"check_repo: {len(fails)} problem(s)" if fails else "check_repo: all checks passed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
