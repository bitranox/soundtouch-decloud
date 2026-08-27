---
name: soundtouch-decloud
description: Use when Bose SoundTouch speakers lost internet radio and presets after Bose shut down the SoundTouch cloud, when setting up a self-hosted replacement service for them, when a speaker is not discovered on the network, when a station's stream URL must be found or verified before it becomes a preset, when presets disappear after every reboot or a preset is accepted but never plays, when a speaker needs telnet or SSH access enabled, or when its service URLs still point at streaming.bose.com.
---

# Bose SoundTouch without the Bose cloud

> This repo is itself a Claude Code plugin/marketplace. Install this skill in any
> project with `/plugin marketplace add bitranox/soundtouch-decloud` then
> `/plugin install soundtouch-decloud`. It is also mirrored in the central bitranox
> marketplace (<https://github.com/bitranox/bitranox-skills>) as
> `infra-soundtouch-decloud`.

Bose shut the SoundTouch cloud down. The speakers keep Bluetooth, AUX and AirPlay, and multiroom
zones still work; internet radio, presets, browsing and Alexa voice commands are dead. Radio and
presets come back once the speakers are pointed at a replacement service you run yourself.

This skill walks an owner through that end to end. Assume the owner is NOT technical: ask, do not
instruct, and ask for the physical things only they can do.

Work through the scripts in `scripts/`. They are what this skill tests and what it uses. AfterTouch
also ships a `soundtouch-cli` covering similar ground; `references/access-and-rooting.md` says where
it goes further, and `references/troubleshooting.md` says where to read upstream when a symptom is
not covered here.

## The two rules that decide the outcome

**Read freely, confirm every change.** Discovery, state, backups and verification run unattended.
Rooting a speaker, rewriting its service URLs, rebooting it and writing presets are explained in
plain words and need a yes first. Say what will change and what it will look like afterwards.

**Order is load-bearing.** Back up the presets BEFORE migrating. A speaker asks its account for
presets shortly after boot but does not mount the radio source until roughly seventy seconds later,
so presets arriving in that window are discarded and its own list comes back empty. The service's
stored copy survives that, but a migration is a different matter and has emptied an account's
presets, so the backup is what stands between the owner and losing them.

## The walkthrough

Ask one question at a time. Prefer multiple choice. Check in after each phase.

| Phase | Do this                                                                            | Detail in             |
|-------|------------------------------------------------------------------------------------|-----------------------|
| 0     | Check the prerequisites and install whatever is missing                            | this file, below      |
| 1     | Ask which speakers and models. Warn about stereo pairs before anything else        | this file, below      |
| 2     | Decide where the service runs and PIN that address                                 | service-setup.md      |
| 3     | Check Docker is installed; if not, walk them through installing it                 | service-setup.md      |
| 4     | Start the container with host networking, verify it answers                        | service-setup.md      |
| 5     | Find the speakers; ask the owner to wake any that do not answer                    | service-setup.md      |
| 6     | Back up every speaker BEFORE any change                                            | presets.md            |
| 7     | Open SSH over the diagnostic port IF something needs it, and make it persist       | access-and-rooting.md |
| 8     | Rewrite the four service URLs, verify nothing cloud is left                        | migration.md          |
| 9     | Wait for the radio sources; bind the account if they never mount                   | migration.md          |
| 10    | Harvest the old presets, ask which stations they still want, validate every stream | presets.md            |
| 11    | Write the presets. Measure before adding anything that rewrites them               | presets.md            |
| 12    | Acceptance: hear two different stations, reboot, check they came back              | presets.md            |

**Phase 0: find out what is already installed, before asking the owner to do anything.**

```bash
python3 scripts/soundtouch_preflight.py
```

Note `python3`, not `uv run`. Every other command here starts with `uv`, and `uv` is one of the
things this checks for, so a checker written the usual way could not run on the machine that needs
it most.

It reports Python, `uv` and Docker, each with a per-platform install instruction when it is
missing, and exits 1 if anything required is absent. `pytest` is reported and never required: it is
for people changing the skill, not using it.

Do not read the result out as a list of failures. Take the missing ones ONE at a time, give the
owner the exact line for their system, and wait for them to say it worked before moving on. A
person handed three terminal commands at once runs none of them. A fresh install usually needs a
new terminal before it is on PATH, which is the commonest reason a second check still says missing.

If the owner has no way to install Docker, say so now rather than at phase 3. The service can run
other ways, and that changes the plan rather than ending it.

**Phase 1, before anything else: ask what you are working with.** Ask whether any two speakers are
a left/right STEREO PAIR, and whether any unit is a Lifestyle or CineMate console rather than a
SoundTouch speaker. Both change what happens later, and neither is visible from the network.

A stereo pair does NOT need to be broken up. The SoundTouch 10 is the only model that supports one,
the shutdown broke it, and AfterTouch restores it through `soundtouch-cli`. Ask so you know the two
halves belong together and can expect them to be re-paired at the end, not so you can dismantle
them.

## Reference files

Use the Read tool to load the file for the phase you are in. Do not answer from this table alone.

| Topic                                                                                                                                                                                       | File                             |
|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------|
| Docker check and install per OS, compose file, host networking, env, discovery, waking a speaker                                                                                            | references/service-setup.md      |
| Diagnostic port 17000, opening SSH over it and its precondition, making it survive a reboot                                                                                                 | references/access-and-rooting.md |
| The four service URLs, the write order, verification, binding an account                                                                                                                    | references/migration.md          |
| Where a stream URL comes from (harvest, research, validate), preset location format, the boot wipe, measuring before automating, alerting instead of auto-repair, backup, the JSON template | references/presets.md            |
| Symptom to cause, the diagnostic one-liners, how long each step takes, upstream docs                                                                                                        | references/troubleshooting.md    |

## Scripts

Run with `uv run scripts/<name>.py`, with ONE exception: `soundtouch_preflight.py` is run as
`python3 scripts/soundtouch_preflight.py`, because `uv` is one of the things it checks for and a
checker that needs the missing tool is no checker at all.

Each prints a JSON envelope; exit 0 yes, 1 no, 2 error. Anything that CHANGES a speaker requires
`--confirm`, so the read half is always safe to run.

| Script                    | Use it to                                                                                        |
|---------------------------|--------------------------------------------------------------------------------------------------|
| `soundtouch_preflight.py` | Report which prerequisites are installed, and how to install the rest. Run it with `python3`     |
| `soundtouch_service.py`   | Check Docker, write and validate the compose file, check service health                          |
| `soundtouch_find.py`      | Discover speakers and report what state each is in                                               |
| `soundtouch_onboard.py`   | Open SSH, migrate the URLs, reboot, prove a preset really played                                 |
| `soundtouch_presets.py`   | Back up, harvest a template from an old backup, validate every stream, restore and check presets |

## When it does not work

Work `references/troubleshooting.md` first: it maps each symptom to its cause, and most reports land
on one of four causes. If the symptom is not there, or the fix does not hold, READ THE UPSTREAM
DOCUMENTATION rather than guessing - the project is actively developed and its guides move ahead of
any local copy:

- Troubleshooting: `https://github.com/gesellix/bose-soundtouch/blob/HEAD/docs/content/docs/guides/TROUBLESHOOTING.md`
- All guides: `https://github.com/gesellix/bose-soundtouch/tree/HEAD/docs/content/docs/guides`
  (GETTING-STARTED, MIGRATION-GUIDE, MIGRATION-SAFETY, DEVICE-INITIAL-SETUP, SELF-HOSTING,
  RASPBERRY-PI, HTTPS-SETUP, MUSIC-SERVICES, SURVIVAL-GUIDE)
- Open issues, for a symptom that looks like a bug rather than a misconfiguration:
  `https://github.com/gesellix/bose-soundtouch/issues`

Fetch the page and act on what it says. Tell the owner plainly when a problem is a known upstream
issue rather than something they did wrong.

## Common mistakes

| Mistake                                                            | What happens                                                                                                                                                                                                               |
|--------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Bridge networking, or adding a `ports:` block                      | Service answers HTTP and discovers nothing. Looks installed, is useless                                                                                                                                                    |
| Migrating before backing up the presets                            | A migration that leaves `bmxRegistryUrl` on the cloud makes the speaker discard every preset and send its empty set back to the service. Observed once, six presets. A REBOOT does not do this - see references/presets.md |
| Installing a repair timer without measuring the wipe first         | A silent writer for a loss that is not happening, which also reverts any station changed on the speaker                                                                                                                    |
| Rewriting only the account URL                                     | Presets sync and nothing ever plays                                                                                                                                                                                        |
| Writing the persisting command before the others                   | Every value reverts at the next reboot although each replied OK                                                                                                                                                            |
| Skipping the persistent marker after opening SSH                   | Access is gone at the next boot and looks like it never worked                                                                                                                                                             |
| Putting the raw stream URL in a preset                             | Accepted at write time, never plays                                                                                                                                                                                        |
| Writing a harvested or researched stream without fetching it first | A station that moved or died is accepted at write time and stays silent. One of six harvested presets was already dead                                                                                                     |
| Treating an `.m3u`/`.pls` link as the stream                       | Served as `audio/x-mpegurl`, so an `audio/` test passes a text file that plays nothing                                                                                                                                     |
| Letting the service's address come from plain DHCP                 | Every speaker breaks at once, weeks later, when the lease changes                                                                                                                                                          |
| Declaring failure 30 seconds after a reboot                        | Readiness ranges 55 to 92 seconds and is per-port; wait 90 s before judging                                                                                                                                                |
