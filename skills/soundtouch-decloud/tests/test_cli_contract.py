"""The promises SKILL.md makes about the scripts, tested by running them.

Every other test file here exercises a pure function. That leaves the CLI surface untested, and it
is the surface the walkthrough and the reference files actually instruct a reader to use: a usage
line that argparse rejects, or a subcommand that prints something other than the documented JSON
envelope, is invisible to a green suite made only of unit tests.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

import soundtouch_find as F
import soundtouch_onboard as O
import soundtouch_presets as P
import soundtouch_service as S

SCRIPTS = {"soundtouch_find.py": F, "soundtouch_service.py": S,
           "soundtouch_presets.py": P, "soundtouch_onboard.py": O}
REFERENCES = Path(__file__).resolve().parent.parent / "references"


# Stops at a backtick (end of a code span), a pipe (a table cell boundary), a shell comment and a
# redirect, all of which end the command rather than belonging to it. The redirect stop is written
# as whitespace, an optional fd number or `&`, then `>` so that it fires on ` > f`, ` >> f` and
# ` 2>&1` while leaving a `<placeholder>` argument alone - those also contain angle brackets, and a
# rule that merely looked for one would eat every documented argument.
INVOCATION = re.compile(r"(?:\S*/)?(soundtouch_\w+\.py)((?:(?!\s*[`|]|\s+#|\s+\d?&?>).)*)")


def _usage_lines(text: str) -> list[tuple[str, list[str]]]:
    """Every script invocation in the text, as (script name, argv), continuations joined.

    Anchored on the script FILENAME rather than on a line starting with `uv run scripts/`, because
    the same instruction appears with a different path prefix and inside a table cell, and matching
    only the tidiest spelling silently skips the others.
    """
    joined = re.sub(r"\\\n\s+", " ", text)
    found: list[tuple[str, list[str]]] = []
    for line in joined.splitlines():
        for name, rest in INVOCATION.findall(line):
            found.append((name, shlex.split(rest.replace("\\", ""))))
    return found


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_every_documented_usage_line_in_the_docstring_parses(name: str) -> None:
    """A usage line the parser rejects is an instruction the reader cannot follow."""
    module = SCRIPTS[name]
    found = _usage_lines(module.__doc__ or "")
    assert found, f"{name} documents no usage line"
    for script, argv in found:
        assert script == name, f"{name} docstring shows {script}"
        module.build_parser().parse_args(argv)


def test_every_documented_usage_line_in_the_reference_files_parses() -> None:
    """The reference files instruct the owner directly, so their command lines must run too."""
    checked = 0
    for path in sorted([*REFERENCES.glob("*.md"), REFERENCES.parent / "SKILL.md"]):
        for name, argv in _usage_lines(path.read_text(encoding="utf-8")):
            if not argv:
                continue  # a bare mention of the file, e.g. the table listing what each one does
            assert name in SCRIPTS, f"{path.name} names an unknown script {name}"
            checked += 1
            SCRIPTS[name].build_parser().parse_args(argv)
    assert checked >= 5, f"only {checked} invocations found; the matcher is probably too narrow"


def _envelope(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """The JSON envelope SKILL.md promises from every subcommand, on stdout and nowhere else."""
    out = capsys.readouterr().out
    body = json.loads(out)
    assert set(body) >= {"ok", "command", "data"}, body
    return body


def test_render_prints_a_json_envelope_not_bare_yaml(capsys: pytest.CaptureFixture[str]) -> None:
    """SKILL.md says every script prints a JSON envelope; render used to print raw YAML."""
    rc = S.main(["render", "--host", "192.0.2.10"])
    body = _envelope(capsys)
    assert rc == 0 and body["ok"] is True
    assert "network_mode: host" in str(body["data"]["compose"])  # type: ignore[index]


def test_render_writes_the_file_and_still_reports_json(tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "docker-compose.yml"
    rc = S.main(["render", "--host", "192.0.2.10", "--out", str(out)])
    body = _envelope(capsys)
    assert rc == 0 and body["ok"] is True
    assert "network_mode: host" in out.read_text(encoding="utf-8")


def test_a_refused_address_exits_2_and_still_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    """2 is reserved for a question left unanswered, so it must not collide with a plain no."""
    rc = S.main(["render", "--host", "127.0.0.1"])
    body = _envelope(capsys)
    assert rc == 2 and body["ok"] is False


def test_docker_missing_is_a_no_not_an_error(monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """A machine without Docker is the case the walkthrough exists to fix, so it exits 1."""
    monkeypatch.setattr(S.shutil, "which", lambda _: None)
    rc = S.main(["check-docker"])
    body = _envelope(capsys)
    assert rc == 1 and body["ok"] is False


def test_every_system_the_guide_names_gets_a_real_install_hint() -> None:
    """The reference table and INSTALL_HINTS are two lists that silently drift apart."""
    table = REFERENCES / "service-setup.md"
    rows = re.findall(r"^\| ([^|]+?) +\| .*Docker Desktop|^\| ([^|]+?) +\|",
                      table.read_text(encoding="utf-8"), re.M)
    systems: list[str] = []
    for row in re.findall(r"^\|\s*([^|]+?)\s*\|", table.read_text(encoding="utf-8"), re.M):
        if row.lower() in {"system", "symptom", "topic", "phase", "mistake", "way", "step", "field"}:
            continue
        if set(row) <= set("- :"):
            continue
        systems += [part.strip() for part in re.split(r",| or ", row) if part.strip()]
    named = [s for s in systems if s.lower() in
             {"windows", "macos", "ubuntu", "debian", "raspberry pi os", "fedora", "rhel",
              "synology", "qnap"}]
    assert named, f"no known system names found in the table (rows: {rows[:3]})"
    for system in named:
        hint = S.install_hint(system)
        assert "Ask which system" not in hint, f"{system!r} has no install hint"


def test_play_refuses_to_start_audio_without_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    """Pressing a preset key starts audio at whatever volume the speaker is on."""
    rc = O.main(["--ip", "192.0.2.31", "play", "--preset", "1", "--expect", "Example Radio"])
    body = _envelope(capsys)
    assert rc == 1 and body["ok"] is False and "--confirm" in str(body["data"])


def test_play_cannot_be_run_without_an_expected_station() -> None:
    """Without --expect an already-playing speaker passes instantly and proves nothing."""
    with pytest.raises(SystemExit) as exit_info:
        O.build_parser().parse_args(["--ip", "192.0.2.31", "play", "--confirm"])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("cmd", ["migrate", "reboot", "enable-ssh"])
def test_the_changing_subcommands_all_require_confirm(cmd: str) -> None:
    args = O.build_parser().parse_args(["--ip", "192.0.2.31", "--service", "http://192.0.2.10:8000",
                                        cmd])
    assert args.confirm is False


def test_restore_requires_confirm_but_check_does_not() -> None:
    common = ["--ip", "192.0.2.31", "--template", "t.json", "--service", "http://192.0.2.10:8000"]
    assert P.build_parser().parse_args(["restore", *common]).confirm is False
    assert not hasattr(P.build_parser().parse_args(["check", *common]), "confirm")


def test_enable_ssh_on_an_unpaired_speaker_refuses_with_an_envelope(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The one precondition the command cannot work without, and the only path that exits 2.

    access-and-rooting.md promises a refusal rather than a silent no-op here, because the injection
    rides a value an unpaired speaker never reads. Both network edges are substituted: a speaker
    with SSH already open returns early, and a reachable one is what makes this path unreachable.
    """
    monkeypatch.setattr(O, "port_open", lambda *_a, **_k: False)
    monkeypatch.setattr(O, "account_uuid", lambda _ip: "")
    rc = O.main(["--ip", "192.0.2.31", "--service", "http://192.0.2.10:8000",
                 "enable-ssh", "--confirm"])
    body = _envelope(capsys)
    assert rc == 2 and body["ok"] is False
    assert "account" in str(body["data"]).lower()


def _accepted(_ip: str, commands: list[str]) -> list[dict[str, object]]:
    """Every telnet command answered and reached its prompt, which is the interesting case."""
    return [{"cmd": c, "reply": "Setting Bose Server URLs\n->", "complete": True} for c in commands]


def _stub_speaker(monkeypatch: pytest.MonkeyPatch, answers: list[bool]) -> None:
    monkeypatch.setattr(O, "account_uuid", lambda _ip: "4376872")
    monkeypatch.setattr(O, "telnet_run", _accepted)
    monkeypatch.setattr(O, "wait_up", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(O.time, "sleep", lambda _s: None)
    replies = iter(answers)
    monkeypatch.setattr(O, "port_open", lambda *_a, **_k: next(replies))


ENABLE = ["--ip", "192.0.2.31", "--service", "http://192.0.2.10:8000", "enable-ssh", "--confirm"]


def test_enable_ssh_waits_for_sshd_instead_of_taking_one_reading(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Readiness is per-port and sshd comes up after the API port, so one check reads slow as no."""
    _stub_speaker(monkeypatch, [False, False, False, True])  # first answer is the already-open guard
    rc = O.main(ENABLE)
    body = _envelope(capsys)
    assert rc == 0 and body["ok"] is True and body["data"]["ssh_open"] is True


def test_a_stored_write_pending_a_reboot_is_a_question_not_a_failure(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Measured on an ST20 27.0.6: the write is stored and only fires once a boot makes it runtime.

    Reporting that as a plain no sent the reader to --full-config, which reboots for a different
    reason and hides which of the two changes did the work.
    """
    _stub_speaker(monkeypatch, [False, False])
    monkeypatch.setattr(O, "SSH_WAIT_STORED", 0.0)
    rc = O.main(ENABLE)
    body = _envelope(capsys)
    assert rc == 2 and body["ok"] is False
    assert "reboot" in str(body["data"]["next"]).lower()


def test_the_full_form_reboots_itself_so_a_closed_port_is_a_definite_no(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The fuller form already rebooted, so there is no later boot left to wait for."""
    _stub_speaker(monkeypatch, [False, False])
    monkeypatch.setattr(O, "SSH_WAIT_FULL", 0.0)
    rc = O.main([*ENABLE, "--full-config"])
    body = _envelope(capsys)
    assert rc == 1 and body["ok"] is False
    assert "serial" in str(body["data"]["next"]).lower()


def test_a_redirect_ends_the_command_but_a_placeholder_argument_does_not() -> None:
    """A documented line may log to a file; the shell part is not argv and must not be parsed."""
    line = ("uv run scripts/soundtouch_presets.py check --ip <speaker-ip> --template <file> "
            "--service <service> >> /var/log/soundtouch-check.log 2>&1")
    (name, argv), = _usage_lines(line)
    assert name == "soundtouch_presets.py"
    assert argv == ["check", "--ip", "<speaker-ip>", "--template", "<file>",
                    "--service", "<service>"]
    P.build_parser().parse_args(argv)
