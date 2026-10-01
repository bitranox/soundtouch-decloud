# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

This repo is the skill's only home, so this file is the only version history it has.

## [Unreleased]

## [1.10.0] 2026-10-01

### Changed

- **`relativize` also converts the legacy `/custom/v1/playback` form.** The AfterTouch player
  (v0.138.1) lists some stations twice in its preset catalog, and the older entry writes this
  host-bound form, which stops playing once the service moves. `relativize` rebuilds the relative
  Orion form from the stream URL its base64 carries, keeping the slot's name and art; a location
  that does not decode is left alone. Reported upstream as gesellix/Bose-SoundTouch#784.

### Added

- **`check` and `restore` name host-bound buttons.** Both compare streams, so a button holding
  the right station in a form that names the service's address read as plainly correct. They now
  list such buttons under `host_bound` with a `warning` pointing at `relativize`; the exit code is
  unchanged, because the station is right.

## [1.9.2] 2026-09-29

### Fixed

- **`health` no longer calls a correct registry foreign when given a loopback address.** Run on
  the service's own machine as `--service http://127.0.0.1:8000`, it compared the registry's
  host with `127.0.0.1` and said `foreign`. A loopback address names no host a speaker uses, so
  the registry is now reported `unjudged` with the reason, and the service itself still counts
  as healthy. Reproduced on a real registry before the fix.

## [1.9.1] 2026-09-29

From converting three installs to the relative form.

### Fixed

- **`onboard play` could prove the wrong station.** A preset key pressed on a speaker in standby
  only wakes it, onto whatever it played last, so the proof measured the old station (and passed
  when the two happened to match). It now wakes the speaker first and presses the preset once it
  is up.
- **`onboard reboot` puts the volume back.** Two ST20s on 27.0.6 came back from a reboot at volume
  10 instead of their 41. The volume is read before and restored after, and both are reported.
- **`onboard reboot` names the likely cause when a speaker does not come back:** one on plain DHCP
  can return on another address, and `soundtouch_find.py` finds it.

### Changed

- **After a registry change, every speaker must be rebooted**, and the docs, `health` and `find`
  now say so. A speaker reads the registry when it starts and keeps the old base URLs until it
  restarts, while `health` and `find` read the service and already report ok. Measured: a relative
  preset on a speaker not restarted since the fix fetched nothing; after one reboot it played.

## [1.9.0] 2026-09-28

Follows AfterTouch v0.138.0, which writes Internet Radio presets in a relative form
(gesellix/Bose-SoundTouch#660, #769). Checked against the upstream tree at v0.138.0.

### Changed

- **Presets are written in the relative Orion form, `/station?data=...`.** The speaker resolves it
  against the `LOCAL_INTERNET_RADIO` base URL its BMX registry names, so a preset keeps playing
  when the service moves to another address. `orion_location` builds it byte for byte as
  upstream's `BuildOrionLocation` does, pinned by golden values from upstream's own Go code. The
  absolute form is still read everywhere and is written by `restore --absolute --service <url>`,
  for firmware that cannot resolve a relative location (verified upstream on 27.0.6 only).
- **`check` and `restore` no longer need `--service`.** The relative form names no host. The
  option is still accepted; `restore` uses it only with `--absolute`.
- **A template holding a relative location is refused as already wrapped**, instead of with the
  misleading "has no stream URL yet".

### Added

- **`soundtouch_presets.py relativize`** stores every absolute Orion preset on a speaker again in
  the relative form, keeping station, name, picture and button, after backing the presets up. The
  same fix AfterTouch's Health page offers.
- **The BMX registry is checked.** `soundtouch_service.py health` exits 1 when the registry names
  another address than the service, and `soundtouch_find.py` reports `registry-foreign` for a
  speaker whose registry does. The cause it names: AfterTouch's persisted `settings.json`
  `server_url` beats `SERVER_URL`, so a copied data directory keeps advertising the machine it was
  copied from, while every speaker still reads migrated. Found on a real install whose registry
  named an address its speakers could not reach; its absolute presets hid it, and converting them
  to the relative form would have silenced every one.

## [1.8.0] 2026-09-27

Everything here comes from a review by the AfterTouch maintainer
(gesellix/Bose-SoundTouch#660), each point checked against the upstream tree at v0.137.1.

### Fixed

- **`check` and `restore` no longer misread presets that AfterTouch wrote.** A slot was compared
  by decoding only the `/custom/v1/playback` form, so a preset in the Orion form that the player
  and `soundtouch-cli` store decoded to nothing and never compared equal. `check` then exited 1
  for it forever, and a scheduled `restore` rewrote the owner's button on every run. Slots are now
  compared by the stream they stand for, in either form or as a bare URL.
- **`harvest` no longer turns a Spotify or library preset into a radio station.** Every slot was
  emitted as `LOCAL_INTERNET_RADIO`, so an album came back as a named hole, and writing it made it
  radio without saying so. A non-radio preset is now carried over exactly as stored and marked
  `keep`. `validate` reports it as `kept`, `check` and `restore` leave its button alone, and
  `harvest` lists it under `kept`.
- **The README promised a preset backup that does not exist.** It said the service writes
  `preset-backups/<MAC>-presets-before-migration.xml` when a speaker migrates. It writes no preset
  backup at all; the only migration-time copies are two configuration files, over SSH, and none
  with `method=telnet`. The README and `references/presets.md` now say what the service keeps:
  `Presets.xml` per device, written on every preset save, not only on a Sync, and the preset
  catalog in `catalog.json`.
- **The add-a-speaker-by-IP command could not work as printed.** `POST /api/setup/devices` needs a
  JSON body `{"ip": ...}` and answers 400 without one. Fixed in `service-setup.md`, where it is the
  only route on Docker Desktop, and added to `migration.md`, which named the step and gave no
  command.
- **The admin gate was described wrongly.** `/api/setup/*` is open whatever `MGMT_PASSWORD` is,
  unless `admin_area_auth` is set to `enabled`, and the service refuses that while the credentials
  are still the published default.
- **The account-id 400 was explained wrongly.** The pair-account body is never read; the 400 is for
  the missing `account_id` query parameter.
- **Stereo pairing is not CLI-only.** The player has done it from a speaker's detail page since
  v0.130.0.
- **`scripts/check_repo.py` read files git never ships.** It walked the filesystem, so a gitignored
  local buffer containing a banned character failed the gate on one machine and never in CI. It now
  asks git for the tracked and new-but-not-ignored files, and walks only outside a git work tree.

### Changed

- **Presets are written in the Orion form**, byte for byte as upstream's `BuildOrionLocation`
  builds it, pinned by golden values produced by upstream's own Go code. The player's catalog, its
  stored-versus-reported comparison and its sharing between speakers now see one format.
  `presets.md` gains a check to run after each AfterTouch update, since the builder is a copy.
- **No more restore on a timer.** The cron line and the systemd unit pair are gone. Since
  AfterTouch v0.137.0 a preset written to one speaker is shared with the others on the account, so
  a repair loop on one box overrules the owner on all of them, and the service now keeps and
  reconciles presets itself. `presets.md` points at those tools in order: the speaker's own fetch,
  the player's "Keep ours / Take the speaker's", "Refresh sources on speaker" and the Health
  QuickFix, and `setup sync --confirm`. The skill keeps its off-service snapshot, a one-shot
  `restore`, and the read-only `check` alarm. `restore` now says in its output that a write can
  reach the other speakers.
- **`render` no longer sets `HTTPS_SERVER_URL`.** The service derives it from `SERVER_URL`, so
  setting it only kept a second copy of the address to go stale. `service-setup.md` also says that
  settings saved in the admin UI take precedence over the compose file's environment.

## [1.7.0] 2026-09-21

### Added

- **Clock display and display language join the timezone as standard phase-7 checks.** A speaker
  factory reset after the Bose cloud shut down, then bound through the replacement service, never
  runs the app's setup, and leaves two more settings at factory values: `clockDisplay` reads
  `timezoneInfo="NOT_SET"`, clock off, 12-hour, and `language` reads `0`. Both sit on the
  speaker's own API on port 8090, so reading and setting them needs no SSH, and the firmware writes
  its own persistent file when they are set. `access-and-rooting.md` gains the GET on every
  speaker, the POST with values copied from a sibling rather than an example, and a refusal to
  guess a language number when there is no sibling to copy from, since the mapping is not
  documented. It also says which absent files to leave alone: the per-speaker cloud token in
  `Marge.xml` cannot be reissued, and `IoT.xml` points at a dead endpoint.

  What was measured and what was not is stated in the text: both POSTs were accepted and persisted
  on a SoundTouch 20 on 27.0.6 without leaving STANDBY, but whether the clock POST alone moves the
  timezone symlink is not known, so the symlink step stays.

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
