# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

The skill is also published in the central bitranox marketplace as
`infra-soundtouch-decloud`, and the two copies are kept in sync. Their version
numbers are independent: this file tracks this repo.

## [Unreleased]

## [1.2.0] 2026-08-27

### Fixed

- **The compose plugin is reported on its own line.** The prerequisite check folded the Docker
  engine and the compose plugin into one result. The verdict was right and the advice was wrong: on
  a distribution where the plugin is a separate package, somebody who had just installed Docker was
  told to install Docker. `docker` now answers for the engine and `docker-compose` for the plugin,
  each with its own instruction: the plugin package on Debian and Fedora, updating Docker Desktop
  on Windows and macOS, where the engine ships it. With no engine at all, compose reports
  `not available` rather than pretending to have probed.

  The old tests missed it because they asserted the verdict, which was correct, and nothing
  asserted what the reader was told to do.

## [1.1.1] 2026-08-27

### Fixed

- **The prerequisite check no longer asks the real machine during tests.** `run_checks` forwarded
  the PATH lookup but not the version lookup, so a test that said "pretend Docker is installed"
  still asked the actual Docker for its version. That passed on Linux and failed on macOS, which
  has no Docker. Both seams are forwarded, and the control asserts every reported version is the
  injected sentinel rather than whatever the machine happens to have.

## [1.1.0] 2026-08-27

### Added

- **`soundtouch_preflight.py`, and a phase 0 that runs it first.** Every command in the skill was
  documented as `uv run ...`, and nothing checked that `uv`, Python or Docker were present or told
  the owner how to get them. The check reports each one with a reason and a per-platform install
  line for whatever is missing, and exits 1 if anything required is absent.

  It imports nothing outside the standard library and is run as
  `python3 skills/soundtouch-decloud/scripts/soundtouch_preflight.py`, because `uv` is one of the
  things it checks for: a preflight written the usual way is unrunnable on exactly the machine that
  needs it. The platform is detected from `/etc/os-release` rather than asked, with `--system` for
  a container, a NAS, or a machine that is not the one in front of you. `pytest` is reported and
  never required.

- **AfterTouch is credited in the README**, which is the service all of this points speakers at.

## [1.0.1] 2026-08-27

### Fixed

- **The conventions gate's own fixtures failed it on Windows.** `write_text` translates newlines to
  the platform separator, so every fixture file arrived with CRLF and the fixture that is supposed
  to pass everything reported three failures. The bytes are the subject of that check, so the
  fixtures pin them. The gate was right and the test was wrong: the real repo passed the same check
  on the same runner, because `.gitattributes` pins `eol=lf`.

## [1.0.0] 2026-08-27

### Added

- **The skill, as its own installable Claude Code plugin marketplace**, so it can be added without
  the whole bitranox collection: `SKILL.md` as the index, five reference files, five scripts over a
  shared standard-library core, and their tests. The scripts take no third-party dependency on
  purpose, so they run on the machine of somebody who is not set up for Python development.

- **`scripts/check_repo.py`**, the repo's own conventions gate: the two plugin manifests must agree
  with the directory they describe, the skill's frontmatter must be the shape the router reads,
  every shipped script must be named by a test, and no tracked text file may carry CRLF or a
  typographic character the house style bans. Each check is tested against a fixture that must fail
  it and a control that must pass.

- **CI** running the tests and that gate on Linux across Python 3.11 to 3.14, plus Windows and macOS.
