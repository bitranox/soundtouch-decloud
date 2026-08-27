"""Tests for the repo conventions gate.

Every check is exercised against a fixture that MUST fail it and against the real repo, which must
pass. A gate that cannot fail is the failure mode worth guarding: it reports success forever and
nobody notices it stopped looking.
"""
import json
import pathlib

import check_repo as G
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


# Every fixture write pins newline="\n": write_text translates \n to the platform
# separator, so on Windows the fixtures arrived with CRLF and failed the line-ending check
# they exist to pass. The bytes are the subject here, so the fixture has to pin them.

SKILL = """---
name: demo-skill
description: Use when demonstrating the gate.
---

# Demo
"""


@pytest.fixture
def repo(tmp_path):
    """A minimal repo of the shape this gate expects, passing every check."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": tmp_path.name, "version": "1.0.0"}), encoding="utf-8", newline="\n")
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": tmp_path.name}]}), encoding="utf-8", newline="\n")
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "tests").mkdir()
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8", newline="\n")
    (skill / "scripts" / "tool.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (skill / "tests" / "test_tool.py").write_text("import tool\n", encoding="utf-8", newline="\n")
    return tmp_path


def test_the_fixture_passes_every_check(repo):
    """The control: without this, a failing test below could just mean the fixture is malformed."""
    assert G.run_checks(repo) == []


# --- manifests ----------------------------------------------------------------------------------

def test_a_version_that_is_not_semver_is_caught(repo):
    path = repo / ".claude-plugin" / "plugin.json"
    path.write_text(json.dumps({"name": repo.name, "version": "1.0"}), encoding="utf-8", newline="\n")
    assert any("not X.Y.Z" in f for f in G.check_manifests(repo))


def test_manifests_naming_different_plugins_are_caught(repo):
    path = repo / ".claude-plugin" / "marketplace.json"
    path.write_text(json.dumps({"plugins": [{"name": "something-else"}]}), encoding="utf-8", newline="\n")
    assert any("marketplace.json lists" in f for f in G.check_manifests(repo))


def test_a_plugin_name_that_is_not_the_repo_dir_is_caught(repo):
    path = repo / ".claude-plugin" / "plugin.json"
    path.write_text(json.dumps({"name": "elsewhere", "version": "1.0.0"}), encoding="utf-8", newline="\n")
    assert any("is not the repo dir" in f for f in G.check_manifests(repo))


def test_unparseable_json_is_a_failure_not_a_traceback(repo):
    (repo / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8", newline="\n")
    assert G.check_manifests(repo)


# --- the skill ------------------------------------------------------------------------------------

def test_a_skill_name_that_is_not_its_directory_is_caught(repo):
    skill = repo / "skills" / "demo-skill" / "SKILL.md"
    skill.write_text(SKILL.replace("demo-skill", "other-name", 1), encoding="utf-8", newline="\n")
    assert any("is not its dir" in f for f in G.check_skill(repo))


def test_a_description_that_does_not_start_with_use_when_is_caught(repo):
    skill = repo / "skills" / "demo-skill" / "SKILL.md"
    skill.write_text(SKILL.replace("Use when demonstrating", "Demonstrates"), encoding="utf-8", newline="\n")
    assert any("must start with 'Use when'" in f for f in G.check_skill(repo))


def test_an_over_long_description_is_caught(repo):
    skill = repo / "skills" / "demo-skill" / "SKILL.md"
    skill.write_text(SKILL.replace("the gate.", "the gate " + "x" * G.DESCRIPTION_MAX),
                     encoding="utf-8", newline="\n")
    assert any("over 1024" in f for f in G.check_skill(repo))


def test_a_second_skill_is_caught(repo):
    second = repo / "skills" / "another"
    second.mkdir()
    (second / "SKILL.md").write_text(SKILL, encoding="utf-8", newline="\n")
    assert any("exactly one" in f for f in G.check_skill(repo))


# --- tests exist ----------------------------------------------------------------------------------

def test_a_script_no_test_names_is_caught(repo):
    (repo / "skills" / "demo-skill" / "scripts" / "orphan.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    assert any("named by no test" in f for f in G.check_tests_exist(repo))


# --- line endings and typographic tells -------------------------------------------------------------

def test_crlf_is_caught(repo):
    (repo / "notes.md").write_bytes(b"line one\r\nline two\r\n")
    assert any("CRLF" in f for f in G.check_line_endings(repo))


@pytest.mark.parametrize("point", [0x2014, 0x2019, 0x201C, 0x2026, 0x00A0, 0x200B, 0xFEFF])
def test_each_banned_character_is_caught(repo, point):
    (repo / "notes.md").write_text("a" + chr(point) + "b", encoding="utf-8", newline="\n")
    assert any(f"U+{point:04X}" in f for f in G.check_no_typographic_tells(repo))


def test_plain_ascii_prose_is_left_alone(repo):
    """The negative control: the tell check must not fire on ordinary text."""
    (repo / "notes.md").write_text("A plain sentence - with a hyphen.\n", encoding="utf-8", newline="\n")
    assert G.check_no_typographic_tells(repo) == []


# --- the real repo ----------------------------------------------------------------------------------

def test_this_repo_passes_its_own_gate():
    assert G.run_checks(REPO) == []


def test_main_returns_zero_on_the_real_repo(capsys):
    assert G.main([str(REPO)]) == 0
    assert "all checks passed" in capsys.readouterr().out


def test_main_returns_one_and_prints_each_problem(repo, capsys):
    (repo / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8", newline="\n")
    assert G.main([str(repo)]) == 1
    assert "FAIL" in capsys.readouterr().out
