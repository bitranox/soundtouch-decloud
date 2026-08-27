"""Tests for the prerequisite check.

The point of this script is that it works on a machine where the tools are MISSING, so most of
these run with the lookup injected and nothing on the real PATH. A checker only tested where
everything is installed has never been asked the question it exists to answer.
"""
import json

import pytest
import soundtouch_preflight as P


def _which(*present):
    """A PATH lookup that finds exactly the named tools and nothing else."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _version(_argv):
    return "1.2.3"


# --- platform detection ---------------------------------------------------------------------

def test_an_explicit_system_wins_over_detection():
    assert P.detect_system("Ubuntu") == "ubuntu"
    assert P.detect_system("  MacOS  ") == "macos"


@pytest.mark.parametrize(("content", "expected"), [
    ('ID=debian\n', "debian"),
    ('ID=ubuntu\nID_LIKE=debian\n', "debian"),
    ('ID=raspbian\nID_LIKE="debian"\n', "debian"),
    ('ID=fedora\n', "fedora"),
    ('ID=rocky\nID_LIKE="rhel centos fedora"\n', "fedora"),
    ('ID=alpine\n', "alpine"),
])
def test_the_linux_family_is_read_from_os_release(tmp_path, content, expected):
    path = tmp_path / "os-release"
    path.write_text(content, encoding="utf-8")
    assert P._linux_family(str(path)) == expected


def test_a_missing_os_release_is_not_a_crash(tmp_path):
    assert P._linux_family(str(tmp_path / "nope")) == "linux"


# --- the individual checks ------------------------------------------------------------------

def test_uv_present_is_reported_with_its_version():
    result = P.check_uv("debian", which=_which("uv"), version=_version)
    assert result["present"] is True
    assert result["detail"] == "1.2.3"


def test_uv_missing_carries_an_install_instruction():
    result = P.check_uv("debian", which=_which(), version=_version)
    assert result["present"] is False
    assert "astral.sh/uv/install.sh" in str(result["install"])


def test_the_uv_instruction_is_the_windows_one_on_windows():
    result = P.check_uv("windows", which=_which(), version=_version)
    assert "install.ps1" in str(result["install"])
    assert "install.sh" not in str(result["install"])


def test_docker_is_present_whenever_the_engine_is_installed():
    result = P.check_docker("debian", which=_which("docker"), version=_version)
    assert result["present"] is True


def test_docker_missing_is_reported_without_running_anything():
    result = P.check_docker("debian", which=_which(), version=_version)
    assert (result["present"], result["detail"]) == (False, "not on PATH")


def test_compose_absent_is_its_own_finding_and_docker_stays_present():
    """The case this split exists for.

    Folding the two together got the verdict right and the advice wrong: somebody who had just
    installed Docker was told to install Docker. Docker must read present, compose must read
    absent, and the instruction must be about the PLUGIN.
    """
    which, version = _which("docker"), lambda argv: "" if "compose" in argv else "1.2.3"
    docker = P.check_docker("debian", which=which, version=version)
    compose = P.check_compose("debian", which=which, version=version)
    assert docker["present"] is True
    assert compose["present"] is False
    assert "docker-compose-plugin" in str(compose["install"])
    assert "get.docker.com" not in str(compose["install"])


def test_compose_is_not_probed_when_docker_is_absent():
    """With no engine there is nothing to ask, and the hint says so for the platform."""
    compose = P.check_compose("windows", which=_which(), version=_version)
    assert (compose["present"], compose["detail"]) == (False, "not available")
    assert "Docker Desktop" in str(compose["install"])


def test_compose_present_reports_its_version():
    result = P.check_compose("debian", which=_which("docker"), version=_version)
    assert (result["present"], result["detail"]) == (True, "1.2.3")


def test_the_docker_instruction_follows_the_platform():
    assert "docker.com" in str(P.check_docker("windows", which=_which(), version=_version)["install"])
    assert "get.docker.com" in str(P.check_docker("debian", which=_which(), version=_version)["install"])


def test_pytest_is_reported_but_never_required():
    result = P.check_pytest(which=_which(), version=_version)
    assert result["required"] is False
    assert result["present"] is False


def test_the_running_python_is_the_one_reported():
    import platform
    assert P.check_python()["detail"] == platform.python_version()


# --- the whole run --------------------------------------------------------------------------

def test_nothing_installed_names_every_required_tool():
    results = P.run_checks("debian", which=_which(), version=_version)
    missing = [r["tool"] for r in results if r["required"] and not r["present"]]
    assert missing == ["uv", "docker", "docker-compose"]


def test_pytest_absent_does_not_make_the_run_fail():
    results = P.run_checks("debian", which=_which("uv", "docker"), version=_version)
    assert [r["tool"] for r in results if r["required"] and not r["present"]] == []


def test_every_check_carries_a_reason_the_owner_can_read():
    for result in P.run_checks("debian", which=_which(), version=_version):
        assert result["why"]


def test_every_missing_required_tool_carries_an_install_instruction():
    """A report that says something is missing and not how to fix it is half a check."""
    for result in P.run_checks("debian", which=_which(), version=_version):
        if not result["present"]:
            assert result.get("install"), result["tool"]


# --- the CLI --------------------------------------------------------------------------------

def test_the_cli_exits_zero_and_prints_an_envelope_on_this_machine(capsys):
    rc = P.main([])
    body = json.loads(capsys.readouterr().out)
    assert set(body) >= {"ok", "command", "data"}
    assert body["command"] == "preflight"
    assert rc in (0, 1)          # depends on what is installed here; the shape is what is asserted
    assert (rc == 0) == body["ok"]


def test_an_unknown_system_still_answers_rather_than_failing(capsys):
    assert P.main(["--system", "haiku"]) in (0, 1)
    body = json.loads(capsys.readouterr().out)
    assert body["data"]["system"] == "haiku"


def test_the_parser_accepts_the_documented_usage():
    assert P.build_parser().parse_args(["--system", "ubuntu"]).system == "ubuntu"
    assert P.build_parser().parse_args([]).system == ""


def test_every_version_reported_comes_from_the_injected_seam():
    """The control for the seam itself.

    Forwarding only the PATH lookup left the version calls hitting the real machine, so a test
    pretending docker was installed still asked the actual docker for its version: it passed
    wherever docker happened to exist and failed on a runner without it. Asserting the reported
    detail is the injected sentinel catches that directly - a half-forwarded seam reports the real
    machine's version string here, or an empty one, and neither is the sentinel.
    """
    results = P.run_checks("debian", which=_which("uv", "docker", "pytest"),
                           version=lambda _argv: "INJECTED")
    assert [r["tool"] for r in results] == ["python", "uv", "docker", "docker-compose", "pytest"]
    for result in results:
        if result["tool"] == "python":
            continue                      # read from the interpreter, not from a subprocess
        assert "INJECTED" in str(result["detail"]), result
        assert result["present"] is True, result
