# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

This repo is the skill's only home, so this file is the only version history it has.

## [Unreleased]

## [1.6.0] 2026-09-21

### Added

- **Setting the timezone is now a standard step for every speaker whose SSH is open.** A speaker's
  zone is the symlink `/mnt/nv/localtime`, whose factory value is `/usr/share/zoneinfo/NOT_SET`,
  and a speaker can come through setup and migration with it still unset. Nothing reports that:
  the box runs on UTC and `date` prints `GMT` where its siblings print their local zone.
  `access-and-rooting.md` gains the check and the one-line repair, guarded by `test -f` so a zone
  the firmware does not ship is never linked, plus what to do when that guard fails. `/mnt/nv` is
  already writable and persistent, so no remount is needed, and running processes pick the change
  up at once. Phase 7 in the walkthrough now names it.

  It is framed as removing a difference from a healthy speaker, not as a cure: the one speaker in a
  six-speaker fleet left on `NOT_SET` was also the only one that kept dropping into SETUP, and the
  text says plainly that nobody has shown one caused the other.

## [1.5.0] 2026-09-20

### Added

- **A speaker's clock can be read without a shell, and the survey now does it.** Its own web server
  writes its system clock into the `Date` header of every response, so `soundtouch_find.py` reads it
  for each speaker and reports `clock-wrong` when the box is more than a day out. Until now a
  speaker with a dead clock passed every check and was reported `ready`, which is exactly wrong:
  nothing structural is broken and no https station will play. The new verdict carries advice in the
  owner's words, including the plain-http station that confirms it.

  The comparison is deliberately coarse. The header renders the box's LOCAL time and then labels it
  GMT, so a correct clock can read a whole UTC offset out; the tolerance is a day, which clears
  every offset on earth and still catches the eleven-year jump a power cut produces. `clock_state`
  and `http_date_header` in `soundtouch_core.py` are the new public functions, and a clock that
  cannot be read leaves the verdict untouched, because not knowing is not a fault.

  This matters most on a speaker whose SSH is closed, where the clock cannot be repaired remotely at
  all - previously the one case with no way even to confirm the diagnosis. `access-and-rooting.md`
  gains the one-request form and how to calibrate it against a speaker you can read both ways.

## [1.4.0] 2026-09-20

### Fixed

- **The Wireless Link Adapter was listed as a device the default enable-ssh form works on. It is
  not.** Measured on one running 20.0.6: both forms were tried in order, each followed by a reboot
  and the full readiness window, and port 22 stayed refused. The injection reached the runtime
  `margeServerUrl` every time, so the write, the persistence and the boot copy all work - that
  firmware simply never passes the value through a shell. It belongs in the group that needs
  serial or U-Boot, and the file now says so, with the tell that distinguishes it from a speaker
  that merely needs more time (`getpdo` shows the injection live while `sshd` is still not
  running) and the instruction to clean the injection off rather than keep escalating.

### Added

- **`enable-ssh --assume-paired`, for firmware whose account field lies.** The precondition reads
  `margeAccountUUID` from `/info` and refuses when it is empty, because a genuinely unpaired
  speaker never reads `margeServerUrl` and the method would do nothing silently. On the Wireless
  Link Adapter on 20.0.6 that field is empty while the speaker is paired and actively fetching
  `/streaming/account/<id>/full` from the service, so the refusal is a false negative that blocks
  the device class most in need of inspection. The bypass is documented with how to confirm the
  pairing from the SERVICE side instead, which is the side that cannot lie about it, and the
  envelope records `precondition_bypassed` so a later reader can tell a skipped check from a
  satisfied one. Four tests, each RED-verified against a mutation that ignores the flag.

### Changed

- **Opening SSH is now recommended rather than described as optional.** The old wording said
  "Opening it is optional. Do not do it to satisfy a checklist", and a test agent given the old
  text and a finished migration duly recommended leaving it closed, correctly quoting that line.

  The reason it is not optional is the clock. A SoundTouch has no battery-backed RTC and, with the
  Bose cloud gone, nothing that sets its time: no init script starts `ntpd`, `/etc` is a read-only
  ubifs so none can be added, and its DHCP client ignores option 42. It keeps good time while
  powered and resets to 2015 on any power cut, after which TLS cannot validate and every HTTPS
  station dies at BUFFERING while a plain-HTTP one plays - with every other check reading green.
  The speaker cannot fix this itself, so the only repair is `ntpd -q` over SSH from outside. A
  speaker whose SSH stays closed loses its radio at the next power cut and cannot be recovered
  remotely.

  The root-access cost is unchanged and still stated; what changed is that the file now puts both
  sides to the owner instead of steering to closed by default. The symptom also has a row in the
  troubleshooting table, because "HTTP plays and HTTPS does not" is what the owner actually sees.

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
