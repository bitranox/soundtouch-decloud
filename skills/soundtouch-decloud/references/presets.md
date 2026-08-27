# Presets: backing them up, writing them, keeping them

## Back up first, always

Do this BEFORE migrating. A migration can empty the account's stored presets - measured here, six
presets lost on one speaker and rebuilt from the backup - and if that happens the only copy left is
this backup. Upstream added a confirmation guard to the destructive Sync path in v0.129.0, so a
current service warns before an overwrite that shrinks a preset set. Back up anyway: the guard
covers Sync, not every route, and it is one command.

```bash
uv run scripts/soundtouch_presets.py backup --ip <speaker-ip> --outdir ./backup
```

Or by hand, straight from the speaker:

```bash
curl -s http://<speaker-ip>:8090/presets > presets-backup.xml
curl -s http://<speaker-ip>:8090/info    > info-backup.xml
```

Check the file has the number of presets the owner expects. Never retype values out of a terminal:
the locations carry query strings that a wrapped terminal line silently truncates.

## Where a station's stream URL comes from

The `location` in a template is the PLAIN stream URL of the station. Producing a correct one per
button is its own job, and it is where a preset that is accepted at write time and never plays
usually comes from. Never write a stream into a preset without fetching it first.

### 1. Harvest what the owner already had

```bash
uv run scripts/soundtouch_presets.py harvest --backup <presets.xml> --out <speaker>.json
```

Two sources for that XML, and the second costs nothing:

- whatever `backup` saved before the migration
- `<data-dir>/preset-backups/<MAC>-presets-before-migration.xml`, which the service writes by itself
  when a speaker migrates. `<data-dir>` is the host path mounted as the service's data volume, so it
  is whatever the compose file says (`service-setup.md`); `<MAC>` is the speaker's device id, which
  `soundtouch_find.py` reports and `/info` carries.

A preset stored while the Bose cloud was alive points at the Orion station adapter and carries the
real stream inside its `data` parameter, base64url JSON with a `streamUrl` key. So the owner's own
old presets usually already CONTAIN the direct stream and there is nothing to search for. A preset
that came from a CATALOGUE source instead, TuneIn and its kind, holds a station id and no stream at
all. `harvest` leaves those as an empty `location` and names them in `needs_research`, keeping the
button and the station name for whatever replaces them.

A template with an empty `location` cannot be written to a speaker: `check` and `restore` refuse
the file and name the button. That is deliberate. A half-researched template is not a set of
presets.

### 2. Ask the owner what they actually want on the buttons

Harvest yields the OLD list. Whether that is still the wanted list is the owner's decision, not
yours, and asking is cheaper than researching six stations they have stopped listening to. Ask it
as a choice:

- the same stations, each with a stream that works again
- some of the old ones, and something different on the rest
- a fresh set, ignoring what the buttons used to hold

Only then go looking, and only for the stations that survive that answer.

### 3. Research the ones that need it

For a station with no recoverable stream, or one whose harvested stream is dead, find the station's
CURRENT direct stream endpoint: search for the station's own streaming page, its published stream
or "listen live" link, or a public radio directory entry. What you want is the endpoint that
returns audio, not the player page that wraps it.

Two things that are not a stream and are easy to mistake for one:

- a **landing page**. `text/html` is a page with a player on it, never a stream.
- a **playlist**. A published "stream link" is very often an `.m3u` or `.pls` that merely LISTS the
  endpoint. It is served as `audio/x-mpegurl`, so a check for `audio/` passes it, and the preset
  built on it plays nothing.

HLS (`.m3u8`, recognisable by `#EXT-X-` lines) is a segment list. Prefer a plain MP3 or AAC endpoint.

### 4. Prove every stream before it reaches a button

```bash
uv run scripts/soundtouch_presets.py validate --template <speaker>.json
```

It fetches each `location`, follows a playlist exactly one level, and reports per button: `audio`
(playable), `playlist`, `hls`, `not-audio`, `dead`, or `missing` for a hole `harvest` left and
nobody has researched yet. `missing` and `dead` are kept apart on purpose: `dead` means the station
moved or ended and needs a replacement stream, `missing` means nobody has looked for one. Exit 0
when every button is playable, 1 when any is not, 2 when the template cannot be read.

Run it from the machine the SERVICE runs on. That is the host that will fetch the stream on the
speaker's behalf, and it is the only vantage point whose answer counts: a stream that resolves from
a laptop on a different network has proved nothing about the one that matters.

This step is not ceremony. Harvesting one household's six pre-shutdown presets and fetching each
one, a station saved years earlier answered 404 while the other five still played. There is no way
to tell those apart without fetching them, and the preset built on the dead one would have been
accepted by the speaker and stayed silent.

## Why a preset that looks right never plays

A `LOCAL_INTERNET_RADIO` preset's `location` does NOT mean "play this URL". The speaker FOLLOWS the
location and expects a station document describing the stream. Give it the stream URL itself and it
receives audio where it expected a document, holds the source about twenty seconds, and discards it
without ever buffering.

The service provides the document at its playback adapter:

```
http://<service-host>:8000/custom/v1/playback/<base64url-of-stream-url>?name=<name>
```

The encoding is URL-safe base64 WITH padding.

Right:

```xml
<ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl"
             location="http://<service-host>:8000/custom/v1/playback/aHR0cHM6Ly8...?name=Example%20Radio"
             sourceAccount="" isPresetable="true"><itemName>Example Radio</itemName></ContentItem>
```

Wrong, and accepted at write time:

```xml
location="https://radio.example.com/stream"
```

This is what `/now_playing` tells you:

| What you see                                 | What it means                             |
|----------------------------------------------|-------------------------------------------|
| source set, no play status, gone after ~20 s | the location is a raw stream URL          |
| buffering, then gone after ~20 s             | the format is right, the audio never came |
| buffering, then playing                      | correct                                   |

Reaching the buffering state is the proof that the preset format and the service are both right.

## Why presets vanish on every reboot

The speaker asks its account for presets shortly after boot, but does not mount the radio source
until roughly seventy seconds later. Presets naming a source that does not exist yet are discarded,
and the speaker's own list comes back empty.

Measured on one speaker on 2026-08-08, on a service build OLDER than 0.129.0. Read it as the shape
to expect rather than as exact numbers for every model, and read the version qualifier as part of
the measurement: it is one speaker on one build, and "Measure it before you automate" below is
there because that is not enough to plan on.

```
+67s  radio source not READY yet    presets on the speaker: 6
+73s  account sync completed        presets on the speaker: 0
      after the source mounted, a scheduled restore rewrote them: 6, stable over 3 minutes
```

The last line is a restore run putting the presets back, NOT the speaker recovering on its own. The
distinction decides whether you need any automation at all, and this trace cannot settle it, because
it was captured with a restore loop already running.

**The service's own copy is NOT overwritten.** That is worth being precise about, because the
opposite is the intuitive reading and it changes what you do about it. A byte-exact capture upstream
shows the service serving the correct preset data at the moment of the reboot resync, with the
stored copy for that device untouched afterwards; the wipe happens after that, inside the speaker's
own firmware, with no further network exchange. So this is recoverable by writing the presets back,
and it does not corrupt the canonical copy.

It is **not fully root-caused** upstream, and what it correlates with is the speaker being one of
several devices under the SAME account. A setup with a distinct account id per speaker has not
reproduced it. If one speaker in a multi-speaker home keeps losing presets, try that before building
any of the automation below.

The treatment is a canonical copy kept off the speaker, rewritten after the source has mounted. One
JSON file per speaker, named by device id:

```json
{
  "deviceId": "00005E005300",
  "name": "Example Speaker",
  "presets": [
    {
      "buttonNumber": 1,
      "name": "Example Radio",
      "location": "https://radio.example.com/stream",
      "contentItemType": "stationurl",
      "source": "LOCAL_INTERNET_RADIO"
    }
  ]
}
```

The `location` here is the PLAIN stream URL. The script builds the playback-adapter wrapping when it
writes, so the service moving to another address never means editing these files.

```bash
# reports, never writes
uv run scripts/soundtouch_presets.py check --ip <speaker-ip> \
    --template <speaker>.json --service http://<service-host>:8000

# writes the buttons that are wrong
uv run scripts/soundtouch_presets.py restore --ip <speaker-ip> \
    --template <speaker>.json --service http://<service-host>:8000 --confirm
```

`check` reports which BUTTONS are wrong, not just which streams are absent. The right station on
the wrong button is still wrong, and comparing streams alone calls that correct.

### Measure it before you automate

Restoring once by hand fixes today. Whether it has to happen again is a QUESTION about this
installation, not a property of SoundTouch, and it is cheap to answer: schedule the read-only
`check` for a week and read what it found. Do that before installing anything that writes.

One line per speaker. `check` reports and never writes, so this is safe to leave running whatever
the answer turns out to be:

```bash
0 * * * * cd /path/to/skill && uv run scripts/soundtouch_presets.py check --ip <speaker-ip> --template <file> --service <service> >> /var/log/soundtouch-check.log 2>&1
```

After a week, read the log rather than your memory of it. Each run appends a JSON envelope, so the
count of bad readings is one command:

```bash
grep -c '"ok": false' /var/log/soundtouch-check.log
```

Zero means the wipe is not happening on this installation and there is nothing to automate. A
non-zero count tells you how OFTEN, which is the number that decides what to do next, and the
envelopes themselves say which speaker and which buttons.

Measured at one site, six speakers, over 18.7 days: a restore loop running every two minutes made
11692 runs and wrote presets exactly ONCE, in its first hour, cleaning up a loss that predated it.
In none of those 11692 runs did a speaker answer while short of its presets. On that evidence the
loop was retired and nothing that writes replaced it.

That is one site on one service build, and it is offered as a reason to measure yours, not as a
result to copy. Two things to try first if the wipe IS happening on yours: give the speaker its own
account id rather than one shared across the home, and open the admin Health tab at
`http://<service-host>:8000/admin`, whose QuickFix pushes the service's stored presets back onto a
speaker without a reboot.

An always-on restore loop is not free, and both costs are quiet:

- **It overrules the owner.** `restore` rewrites any button whose stream does not match the
  template, so a station retuned ON THE SPEAKER is reverted at the next run, within two minutes.
  The template file is the authority and nothing announces that.
- **It hides the event you wanted to know about.** A loss that self-heals in two minutes is a loss
  nobody ever hears about, so nobody can tell whether the loop is still earning its place, or
  whether the service update three months ago fixed the wipe outright.

If a measured week shows a real, repeating wipe, the schedule below is the fallback. Every two
minutes is deliberate: it is a no-op when the presets are already correct, and it does nothing at
all while the radio source is not mounted, because writing in that window is silently undone by the
same wipe.

| System              | How                                                                                                                                                                                        |
|---------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Linux, Raspberry Pi | `crontab -e`, then one line per speaker: `*/2 * * * * cd /path/to/skill && uv run scripts/soundtouch_presets.py restore --ip <speaker-ip> --template <file> --service <service> --confirm` |
| Linux with systemd  | The unit pair below                                                                                                                                                                        |
| Synology, QNAP      | The Task Scheduler in the web interface, a user-defined script every 2 minutes                                                                                                             |
| Windows             | Task Scheduler, a basic task repeating every 2 minutes                                                                                                                                     |
| macOS               | A launchd agent with `StartInterval` 120                                                                                                                                                   |

```ini
# /etc/systemd/system/soundtouch-presets.service
[Unit]
Description=Restore Bose SoundTouch presets after a speaker boots

[Service]
Type=oneshot
WorkingDirectory=/path/to/skill
ExecStart=/usr/bin/uv run scripts/soundtouch_presets.py restore --ip <speaker-ip> --template <file> --service <service> --confirm
# exit 1 is "presets were short", which is the normal reason this unit exists
SuccessExitStatus=1
```

```ini
# /etc/systemd/system/soundtouch-presets.timer
[Unit]
Description=Restore Bose SoundTouch presets every two minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
Persistent=true

[Install]
WantedBy=timers.target
```

Check afterwards that it is actually running, rather than assuming: wait for the next slot and look
for a change, or run the `check` subcommand and confirm it reports nothing missing.

### Alert, do not auto-repair

What an owner actually needs is to be TOLD when presets go missing. A writer that repairs silently
answers a different question, and answers it in a way that destroys the evidence.

Build the alarm on `check`, which never writes, and act on its EXIT CODE. The two failures are not
one failure and must not share a threshold:

| Exit | What it found                                         | How patient to be                                                     |
|------|-------------------------------------------------------|-----------------------------------------------------------------------|
| 0    | every speaker answered, every button holds its stream | nothing to do                                                         |
| 1    | a speaker ANSWERED and is short of its presets        | this is the real thing. Report the first one                          |
| 2    | a speaker could not be read at all                    | usually asleep or on flaky WiFi. Require a long streak before mailing |

Collapsing 1 and 2 into one "it failed" branch is the mistake that makes the alarm useless, and it
is the shape a shell `if` falls into by default. Measured at the same site: one WiFi speaker that
sleeps produced 1303 unreadable readings out of 11692 while never once being short of presets. An
alarm that mailed on any non-zero exit would have been almost entirely that speaker napping, and
the owner would have learned to ignore it before a real loss ever arrived.

A workable shape, hourly:

- Keep a per-failure STREAK across runs in a small state file.
- Exit 1 mails at a streak of 1. A preset loss is rare and does not heal on its own.
- Exit 2 mails only after most of a day of consecutive failures. At an hourly cadence, 8 is a
  reasonable default and 24 is a quiet one; pick from what your own week of readings shows.
- Hold a due alert for a few minutes and re-check before sending it, because a speaker that has
  been unreachable for half an hour can be back three minutes later.
- Sit out a cooldown after mailing, and send one message when it recovers.
- Say WHICH failure it is in the subject. They need different responses: a short speaker wants
  a `restore` run, an unreachable one wants someone to look at the network.

**`check` does not prove the service is up.** It reads the speaker's stored presets and compares
them with the template, and it never contacts the service to do it, so a service that died after a
`:latest` pull leaves `check` reporting 0 on every speaker while nothing would actually play. The
alarm above watches preset drift and reachability, not the service. Watch the service separately,
and pin its image rather than tracking `:latest` if a silent update is the thing being guarded
against.

`validate` is the other half, on its own schedule. It fetches the stations themselves, which is the
one failure neither the speaker nor the service can report: a station can go off the air with
everything locally correct. Weekly is enough.

Tell the owner what to expect either way. With an alarm and no repair loop, presets that vanish
STAY vanished until someone runs `restore`, and that is the point: they find out.

## Acceptance: listen, do not count

Counting presets proves they were written, not that they play.

1. Turn the volume down first.
2. Play one preset and watch until it reaches the playing state.
3. Play a DIFFERENT preset and watch again. One working station does not prove the set works.
4. Put the volume back.
5. Reboot, wait three minutes, and check what the presets do on their own. This step MEASURES; it
   does not presuppose an answer. Presets still there is the common result and means no repair
   automation is needed. Presets gone is the wipe, and sends you to "Measure it before you
   automate" rather than straight to a timer.

When checking that a second preset played, require the station NAME to change. Waiting only for the
playing state passes instantly when the speaker is already playing the previous preset, which proves
nothing at all.
