# When it does not work

Start at the service's own health tab, `http://<service-host>:8000/admin`. It checks whether a
speaker's URLs still point at the dead cloud, which is the most common cause by a wide margin.

## Symptom to cause

| Symptom                                                     | Cause                                                        |
|-------------------------------------------------------------|--------------------------------------------------------------|
| Service starts, finds no speakers at all                     | Bridge networking. Discovery is multicast; use host networking |
| One speaker missing, others found                            | Asleep, on a guest network, or on a different subnet          |
| `/sources` lists no radio source                             | `bmxRegistryUrl` still points at the dead cloud               |
| All four URLs local, still no radio source                   | No account bound; check `margeAccountUUID`                    |
| Presets accepted, gone after every reboot                    | The boot wipe. Try a per-speaker account id before automating around it |
| Preset selected, nothing plays, gives up after ~20 s          | The location is a raw stream URL, not the playback adapter    |
| Buffering, then gives up after ~20 s                          | Format is right. Either the audio never arrived, or upstream issue #604 |
| Everything worked, then all speakers broke at once            | The service's address changed                                 |
| Values written, all replied OK, gone after reboot             | `envswitch` was written before the `sys configuration` writes |
| SSH worked, gone after a reboot                               | The flash marker was never written                            |
| Speaker plays but ignores presets, source reads LOCAL         | A Lifestyle console sitting on its own input, not SoundTouch  |

## One question per command

```bash
# What does the speaker actually have? (the live layer)
printf 'getpdo CurrentSystemConfiguration\r\n' | nc <speaker-ip> 17000

# What will apply after a reboot?
curl -s http://<speaker-ip>:8090/info | grep -o '<margeURL>[^<]*'

# Which sources are mounted? Read the status ATTRIBUTE; the tags are self-closing
curl -s http://<speaker-ip>:8090/sources

# What presets does it hold, and in what format?
curl -s http://<speaker-ip>:8090/presets

# What is it doing right now?
curl -s http://<speaker-ip>:8090/now_playing

# Is it bound to an account?
curl -s http://<speaker-ip>:8090/info | grep -o '<margeAccountUUID>[^<]*'
```

The live layer and `/info` CAN disagree straight after a `sys configuration` write. That is not a
fault: one is what is running, the other is what survives a reboot.

The persisted layer cannot be read back at all. `envswitch` answers `Invalid Command Option` to a
read; it is write-only, and only a reboot reveals what it holds.

## How long things take

Measured over ten observed reboots, and it is a RANGE, not a moment:

| Step                                      | Time                            |
|-------------------------------------------|---------------------------------|
| `sys reboot` until the speaker drops       | 2.3 to 5.3 s                    |
| Able to answer `getpdo` over 17000 again   | 55 to 92 s, median about 70 s   |
| Radio sources ready                        | after that, not with it         |

**Readiness is per-port, not a single moment.** Port 8090 and the diagnostic port do not come back
together: waiting for 8090 and then immediately reading over 17000 returns an empty response on a
box that is otherwise up. A booting speaker also answers a bare HTTP 400 with an empty body before
its services are ready, which reads as a rejection rather than as "too early". Wait for the
interface you are about to use, not for a different port to answer first.

**Do not call anything broken before 90 seconds**, and after a power cycle give it a full 2 minutes
before concluding a port is genuinely closed rather than slow. Most "it did not work" reports are a
check made thirty seconds after a reboot.

Presets returning after a power cut depends entirely on how often your restore runs, so it is a
property of your own schedule and not of the speaker.

## Still stuck: read the upstream documentation

The project is actively developed and its own guides move ahead of anything written here. When the
table above does not cover the symptom, or the fix does not hold, FETCH AND READ these rather than
guessing:

- `https://github.com/gesellix/bose-soundtouch/blob/HEAD/docs/content/docs/guides/TROUBLESHOOTING.md`
- `https://github.com/gesellix/bose-soundtouch/tree/HEAD/docs/content/docs/guides` for the rest:
  GETTING-STARTED, MIGRATION-GUIDE, MIGRATION-SAFETY, DEVICE-INITIAL-SETUP, SELF-HOSTING,
  RASPBERRY-PI, HTTPS-SETUP, MUSIC-SERVICES, SURVIVAL-GUIDE, CLI-REFERENCE
- `https://github.com/gesellix/bose-soundtouch/tree/HEAD/docs/content/docs/analysis` for how the
  firmware actually behaves, and `.../docs/content/docs/reference` for the model support matrix and
  the API endpoint list. Those two directories hold most of what is not in `guides`.
- `https://github.com/gesellix/bose-soundtouch/issues` when it looks like a bug rather than a
  misconfiguration. Several behaviours here are known upstream issues, including radio sources not
  activating after an in-place migration.

Read the page and act on what it says. If it turns out to be a known upstream issue, tell the owner
that plainly: it saves them hunting for a mistake they did not make.

## The service's own record of what happened

With `RECORD_INTERACTIONS` on, every request a speaker made is on disk under the data directory, in
UTC. That answers "did the speaker ever actually call us" definitively, which no amount of
inspecting the speaker can.

The timestamps are UTC and the container's clock is UTC. Correlating them with something seen at the
speaker means converting to local time first; comparing them raw comes out hours apart and invites a
confident wrong conclusion.
