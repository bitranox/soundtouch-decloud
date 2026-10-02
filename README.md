# soundtouch-decloud

Bose shut the SoundTouch cloud down. The speakers keep Bluetooth, AUX, AirPlay and multiroom
zones; internet radio, presets, browsing and Alexa voice commands stopped working. This repo is a
Claude Code skill that walks an owner through pointing the speakers at a replacement service they
run themselves, and getting radio and presets back.

It assumes the person at the other end is not technical. The skill asks rather than instructs,
reads freely, and requires a yes before anything changes on a speaker.

## Credit

The replacement service this skill points speakers at is **AfterTouch**, by
[gesellix](https://github.com/gesellix): <https://github.com/gesellix/Bose-SoundTouch>. None of this
works without it. The service, its migration guides and its troubleshooting docs are the ground
truth, and where this skill and that project disagree, that project is right. If you get value out
of the skill, the thanks belong there.

This repo is a skill, not a fork: it carries no part of AfterTouch and installs the published
container image.

## What you need

- One or more Bose SoundTouch speakers on the local network.
- A machine on the same network that can run Docker: a NAS, a Raspberry Pi, a small Linux box.
  It hosts the replacement service and needs an address that does not move.
- Python 3.11 or newer, `uv`, and Docker with the compose plugin, on that machine.

You do not have to work any of that out. Checking it is the skill's first step: it runs the check
itself, tells you in plain words what is missing, and gives you the install line for your own
system, one thing at a time. If something cannot be installed, it says so then rather than halfway
through.

## Install the skill in Claude Code

This repo is itself a Claude Code plugin marketplace, so it installs directly:

```
/plugin marketplace add bitranox/soundtouch-decloud
/plugin install soundtouch-decloud
```

## Use it

Describe the problem in your own words and Claude loads the skill. Anything like "my Bose
SoundTouch presets stopped working", "the speakers lost internet radio", or "set up the
self-hosted Bose service" matches. You can also ask for it by name:

```
Use the soundtouch-decloud skill to get my SoundTouch speakers working again.
```

From there it works in phases, checking in after each one: check the prerequisites and help you
install whatever is missing, ask which speakers you have, stand up the service, find the speakers,
back up every speaker BEFORE anything changes, open SSH only if something needs it, rewrite the
four service URLs, wait for the radio sources, recover and verify the stations, write the presets,
then prove it by listening rather than by counting.

If you would rather see the prerequisite check before installing anything, clone this repo and run
it directly. It needs nothing but Python 3.9 or newer, because `uv` is one of the things it looks
for, and it says so when that Python is older than the 3.11 the other scripts need:

```bash
python3 skills/soundtouch-decloud/scripts/soundtouch_preflight.py
```

Order matters more than it looks. The backup comes first because a migration has emptied an
account's presets, and the four URLs have a write order that decides whether any of them survive
the next reboot.

## What it can do

The skill carries five reference files and five scripts. Every script prints a JSON envelope and
uses the same exit codes: 0 yes, 1 no, 2 could not tell. Anything that CHANGES a speaker needs an
explicit `--confirm`, so the read half is always safe to run.

| Script                    | What it does                                                                                                       |
|---------------------------|--------------------------------------------------------------------------------------------------------------------|
| `soundtouch_preflight.py` | Report which prerequisites are installed and how to install the rest (run with `python3`)                          |
| `soundtouch_service.py`   | Check Docker, write the compose file, check the service answers and its registry names this address                |
| `soundtouch_find.py`      | Discover speakers on the network and give each one a verdict on what to do next                                    |
| `soundtouch_onboard.py`   | Report migration state, open SSH over the diagnostic port, rewrite the service URLs, reboot, prove a preset played |
| `soundtouch_presets.py`   | Back up, harvest, validate, check, restore and relativize presets                                                  |

Beyond the mechanics, the skill knows the things that are easy to get wrong and hard to diagnose:

- **Bridge networking looks installed and finds nothing.** The service answers HTTP but cannot
  discover speakers, because discovery is multicast. On Linux use host networking (the default of
  `render`); on Docker Desktop for Windows or macOS use `render --network ports` and add each
  speaker by IP.
- **Rewriting only the account URL** produces a speaker that registers, syncs presets and plays
  nothing, because radio source types arrive through a different URL.
- **The URL write order is load-bearing.** Persisting before writing saves the OLD values, and
  every command is still accepted.
- **A raw stream URL in a preset is accepted at write time and never plays.** The speaker follows
  the location expecting a station document, not audio. Presets are written in AfterTouch's
  relative form (`/station?data=...`), which the speaker resolves through the service's registry,
  so they keep playing when the service moves. `check` and `restore` name any button whose
  location still carries a host (the absolute form, or a decodable legacy `/custom/v1/playback` form)
  under `host_bound`, and `relativize` stores those again in the relative form.
- **A copied service keeps the old address.** Its `settings.json` `server_url` beats the
  `SERVER_URL` it is started with, so the registry sends every speaker to the machine it was
  copied from. `soundtouch_service.py health --service <service>` checks for it.
- **A service address from plain DHCP** breaks every speaker at once, weeks later.

### Recovering the stations

Getting a working stream URL for each button is its own job, and it is where a preset that looks
right but stays silent usually comes from. The skill does it in four steps:

1. **Harvest.** A preset stored while the Bose cloud was alive carries the real stream URL inside
   it, so the owner's own presets usually already contain what is needed and there is nothing to
   search for. That needs the presets as they were BEFORE the migration, and the replacement
   service does not keep them: it stores each speaker's presets as it sees them from then on
   (`accounts/<account>/devices/<id>/Presets.xml` in its data directory), and at migration it
   saves no presets at all: over SSH it keeps two configuration files, over telnet not even those.
   So the skill runs `backup` before it migrates anything.

   ```bash
   uv run skills/soundtouch-decloud/scripts/soundtouch_presets.py harvest --backup <presets.xml> --out <speaker>.json
   ```

   A preset that came from a catalogue source instead holds a station id and no stream. Those come
   back as named holes rather than being dropped, and a template with holes cannot be written to a
   speaker until they are filled. A preset that is not radio at all, a Spotify album or a library
   track, is carried over exactly as stored and left alone.

2. **Ask.** The harvest gives the OLD station list. Whether that is still the wanted list is the
   owner's decision, so the skill asks before anyone researches anything.

3. **Research** whatever is left, looking for the station's current direct stream endpoint rather
   than the player page that wraps it.

4. **Validate**, from the machine that runs the service, because that is the host that will fetch
   the stream:

   ```bash
   uv run skills/soundtouch-decloud/scripts/soundtouch_presets.py validate --template <speaker>.json
   ```

   It reports per button: `audio` (playable), `playlist`, `hls`, `not-audio`, `dead`, `missing`
   for a hole nobody has researched yet, or `kept` for a non-radio preset it does not fetch. Stations move and die: of six presets recovered from one
   household's pre-shutdown backup, one had already gone dead. An `.m3u` playlist is the other
   trap, because it is served as `audio/x-mpegurl` and passes a naive check for `audio/` while
   containing no audio at all.

### Watching it, without silently repairing it

The service keeps each speaker's presets and hands them back when the speaker fetches them, and
its player shows where what it stores and what a speaker reports disagree, with a per-button
choice between the two. The skill points there for day-to-day repair and installs nothing that
writes on its own: since AfterTouch v0.137.0 a preset written to one speaker is shared with the
others on the account, so a repair loop on one box overrules the owner on all of them.

What the skill adds is a snapshot of each speaker's presets kept off the service, a one-shot
`restore` from it, and a read-only `check` to run on a schedule as an alarm. Measured at one
site, a restore loop running every two minutes for weeks wrote presets exactly once, in its first
hour, cleaning up a loss that predated it. So measure before automating anything. The alarm keeps
"a speaker is short of presets" and "a speaker did not answer" apart, because they need different
patience: at that same site one sleeping WiFi speaker was regularly unreadable while never once
being short.

## Run the tests

The scripts are standard library only, by design, so that they run on a stranger's machine with
nothing installed. The only test dependency is pytest:

```bash
python -m pip install pytest
python -m pytest -q
```

`scripts/check_repo.py` checks the repo's own conventions: the manifests agree with the directory
they describe, the skill's frontmatter is the shape the router needs, every shipped script is
named by a test, and nothing arrived with CRLF or a typographic character (em-dash, curly quote,
ellipsis, non-breaking space, BOM). CI runs the tests on Linux, Windows and macOS, and the
conventions gate on Linux.

## Changelog

Every release is described in [CHANGELOG.md](CHANGELOG.md).

## Layout

```
skills/soundtouch-decloud/
  SKILL.md          the index and the walkthrough
  references/       service setup, access and rooting, migration, presets, troubleshooting
  scripts/          the five tools plus their shared core
  tests/            their tests
scripts/check_repo.py   the repo conventions gate
scripts/tests/          its tests
```

## License

MIT. See [LICENSE](LICENSE).
