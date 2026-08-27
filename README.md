# soundtouch-decloud

Bose shut the SoundTouch cloud down. The speakers keep Bluetooth, AUX, AirPlay and multiroom
zones; internet radio, presets, browsing and Alexa voice commands stopped working. This repo is a
Claude Code skill that walks an owner through pointing the speakers at a replacement service they
run themselves, and getting radio and presets back.

It assumes the person at the other end is not technical. The skill asks rather than instructs,
reads freely, and requires a yes before anything changes on a speaker.

## What you need

- One or more Bose SoundTouch speakers on the local network.
- A machine on the same network that can run Docker: a NAS, a Raspberry Pi, a small Linux box.
  It hosts the replacement service and needs an address that does not move.
- Python 3.11 or newer, for the scripts.

## Install the skill in Claude Code

This repo is itself a Claude Code plugin marketplace, so it installs directly:

```
/plugin marketplace add bitranox/soundtouch-decloud
/plugin install soundtouch-decloud
```

The same skill also ships in the central bitranox marketplace as `infra-soundtouch-decloud`:

```
/plugin marketplace add bitranox/bitranox-skills
/plugin install bitranox
```

Install one or the other, not both.

## Use it

Describe the problem in your own words and Claude loads the skill. Anything like "my Bose
SoundTouch presets stopped working", "the speakers lost internet radio", or "set up the
self-hosted Bose service" matches. You can also ask for it by name:

```
Use the soundtouch-decloud skill to get my SoundTouch speakers working again.
```

From there it works in phases, checking in after each one: find the speakers, stand up the
service, back up every speaker BEFORE anything changes, rewrite the four service URLs, wait for
the radio sources, recover and verify the stations, write the presets, then prove it by listening
rather than by counting.

Order matters more than it looks. The backup comes first because a migration has emptied an
account's presets, and the four URLs have a write order that decides whether any of them survive
the next reboot.

## What it can do

The skill carries five reference files and four scripts. Every script prints a JSON envelope and
uses the same exit codes: 0 yes, 1 no, 2 could not tell. Anything that CHANGES a speaker needs an
explicit `--confirm`, so the read half is always safe to run.

| Script                  | What it does                                                                              |
|-------------------------|-------------------------------------------------------------------------------------------|
| `soundtouch_service.py` | Check Docker, write and validate the compose file, check the service is answering          |
| `soundtouch_find.py`    | Discover speakers on the network and report the state each one is in                       |
| `soundtouch_onboard.py` | Open SSH over the diagnostic port, rewrite the service URLs, reboot, prove a preset played |
| `soundtouch_presets.py` | Back up, harvest, validate, check and restore presets                                      |

Beyond the mechanics, the skill knows the things that are easy to get wrong and hard to diagnose:

- **Bridge networking looks installed and is useless.** The service answers HTTP and discovers
  nothing. It needs host networking.
- **Rewriting only the account URL** produces a speaker that registers, syncs presets and plays
  nothing, because radio source types arrive through a different URL.
- **The URL write order is load-bearing.** Persisting before writing saves the OLD values, and
  every command still answers OK.
- **A raw stream URL in a preset is accepted at write time and never plays.** The speaker follows
  the location expecting a station document, not audio.
- **A service address from plain DHCP** breaks every speaker at once, weeks later.

### Recovering the stations

Getting a working stream URL for each button is its own job, and it is where a preset that looks
right but stays silent usually comes from. The skill does it in four steps:

1. **Harvest.** A preset stored while the Bose cloud was alive carries the real stream URL inside
   it, so the owner's own presets usually already contain what is needed and there is nothing to
   search for. The replacement service also writes a `preset-backups/<MAC>-presets-before-migration.xml`
   by itself when a speaker migrates, so this often works even for someone who never exported
   anything.

   ```bash
   uv run scripts/soundtouch_presets.py harvest --backup <presets.xml> --out <speaker>.json
   ```

   A preset that came from a catalogue source instead holds a station id and no stream. Those come
   back as named holes rather than being dropped, and a template with holes cannot be written to a
   speaker until they are filled.

2. **Ask.** The harvest gives the OLD station list. Whether that is still the wanted list is the
   owner's decision, so the skill asks before anyone researches anything.

3. **Research** whatever is left, looking for the station's current direct stream endpoint rather
   than the player page that wraps it.

4. **Validate**, from the machine that runs the service, because that is the host that will fetch
   the stream:

   ```bash
   uv run scripts/soundtouch_presets.py validate --template <speaker>.json
   ```

   It reports per button: `audio` (playable), `playlist`, `hls`, `not-audio`, `dead`, or `missing`
   for a hole nobody has researched yet. Stations move and die: of six presets recovered from one
   household's pre-shutdown backup, one had already gone dead. An `.m3u` playlist is the other
   trap, because it is served as `audio/x-mpegurl` and passes a naive check for `audio/` while
   containing no audio at all.

### Watching it, without silently repairing it

The skill's advice is to MEASURE before installing anything that writes. Schedule the read-only
check for a week and read what it found. Measured at one site over 18.7 days, a restore loop
running every two minutes made 11692 runs and wrote presets exactly once, in its first hour,
cleaning up a loss that predated it.

An always-on repair loop is not free: it reverts any station retuned on the speaker itself, and it
hides the event you wanted to know about. The skill describes an alarm instead, built on the exit
codes above, keeping "a speaker is short of presets" and "a speaker did not answer" apart. They
need different patience: at that same site one sleeping WiFi speaker produced 1303 unreadable
readings out of 11692 while never once being short.

## Run the tests

The scripts are standard library only, by design, so that they run on a stranger's machine with
nothing installed. The only test dependency is pytest:

```bash
python -m pip install pytest
python -m pytest -q
```

`scripts/check_repo.py` checks the repo's own conventions: the manifests agree with the directory
they describe, the skill's frontmatter is the shape the router needs, every shipped script is
named by a test, and nothing arrived with CRLF. CI runs both on Linux, Windows and macOS.

## Layout

```
skills/soundtouch-decloud/
  SKILL.md          the index and the walkthrough
  references/       service setup, access and rooting, migration, presets, troubleshooting
  scripts/          the four tools plus their shared core
  tests/            their tests
scripts/check_repo.py   the repo conventions gate
```

## License

MIT. See [LICENSE](LICENSE).
