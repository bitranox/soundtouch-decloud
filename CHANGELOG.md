# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

This repo is the skill's only home, so this file is the only version history it has.

## [Unreleased]

## [1.3.0] 2026-09-20

### Added

- **The troubleshooting reference can now name the fault where a speaker answers everything and
  plays nothing.** A speaker can pass every check the skill teaches - all four URLs local, account
  bound, presets in the adapter format, `LOCAL_INTERNET_RADIO` READY, wired and reaching the
  internet - and still be silent, because its own state machine is stuck in setup. `/now_playing`
  is the only endpoint that shows it, as `source="SETUP"` before anyone touches the unit and as
  `EVENT_IN_WRONG_STATE ... Inactive` after a button has been pressed. The distinguishing tell is
  that the speaker issues no request at all, so the service's interaction record stays empty and
  inspecting the service correctly finds nothing wrong.

  The fix is a POWER key press over HTTP, which clears it in about a second. The section says
  plainly not to reach for a reboot or a power cycle first, because both work and both cost the
  55-to-92-second readiness window for nothing.

  Measured rather than asserted. Given the previous text, a test agent ruled out every documented
  cause correctly and then recommended pulling the power for ten seconds and waiting two minutes,
  at its own stated medium confidence, noting that "the reference has no row for this" and that
  "the reference is silent on both states". Given the new text it named the fault directly, gave
  the key press, said not to power-cycle first, and quoted the lines it rested on.

  Two limits are stated in the text rather than papered over: only the HTTP path was measured, so
  whether the physical button clears the same state is untested, and what puts a speaker into the
  state in the first place is not established.

### Changed

- **The skill is no longer mirrored in the central bitranox marketplace.** It was removed there in
  that repo's 7.0.0. This repo is the only place it ships, which is what the install instructions
  in the README and the skill now say. Nothing about the skill's content changed with the move.

## [1.2.3] 2026-08-27

### Fixed

- **A preset backup the service never writes was offered as a file to go and read.**
  `references/presets.md` named `<data-dir>/preset-backups/<MAC>-presets-before-migration.xml` as
  something written automatically on migration. It is not, in any spelling, anywhere upstream. What
  is written automatically is `SoundTouchSdkPrivateCfg.xml.bak` and `hosts.bak`, and on the telnet
  path this skill mandates, the run returns before even that step. The real preset file is
  `accounts/<account>/devices/<serial>/Presets.xml`, written on Sync rather than on migration. The
  CLI help string carried the same wrong name and is corrected with it.
- **The render sample omitted `MGMT_USERNAME` and `MGMT_PASSWORD`,** which the generator always
  emits, while the paragraph below it tells the reader to change `MGMT_PASSWORD`.

## [1.2.2] 2026-08-27

### Fixed

- **The README no longer hands the reader a command they cannot run.** It told them to run the
  prerequisite check at `skills/soundtouch-decloud/scripts/...`, which only resolves in a clone of
  this repo. Somebody who installed the plugin has it in the Claude Code plugin cache, so the path
  was wrong for the normal install, and asking a reader to type a path at all is the wrong shape
  for a skill they installed so they would not have to. Running the check is the skill's first
  step, and the README now says that. The direct command stays, for anyone who cloned the repo and
  wants to see the answer before installing anything.

## [1.2.1] 2026-08-27

### Fixed

- **The compose row is named after the command, not the package.** It read `docker-compose`, which
  is also the deprecated standalone v1 binary. The install line beside it was correct, but the
  label is the string a reader searches, and that search reaches v1. It reads `docker compose` now,
  which is what the skill runs and what the owner types.

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
