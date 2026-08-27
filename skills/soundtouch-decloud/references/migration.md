# Pointing a speaker at the local service

## A speaker carries FOUR service URLs, not one

| Field            | What it does                              | Value after migration                                |
|------------------|-------------------------------------------|------------------------------------------------------|
| `margeServerUrl` | Account, presets, device registration     | `http://<service-host>:8000`                         |
| `bmxRegistryUrl` | The list of source TYPES, so: radio        | `http://<service-host>:8000/bmx/registry/v1/services` |
| `statsServerUrl` | Telemetry                                 | `http://<service-host>:8000`                         |
| `swUpdateUrl`    | Software update                           | `http://<service-host>:8000/updates/soundtouch`      |

Rewriting only the account URL is the single most common mistake, and it produces a speaker that
looks migrated: it registers, it syncs presets, and it plays nothing at all. Radio source types
reach the speaker ONLY through `bmxRegistryUrl`. While that still points at the dead cloud the
speaker mounts no radio source, discards every preset that names one, and then sends its empty
preset set back to the service.

## The write order decides whether any of it survives

Write the four `sys configuration` commands FIRST, then `envswitch boseurls set`, then reboot.

`envswitch boseurls set` does not only set its own two values: it SAVES the current runtime state to
the layer that survives a reboot. Run it first and it saves the OLD values, so every `sys
configuration` written afterwards is discarded at the next boot even though each one answered `OK`.
Nothing reports this. The speaker simply comes back on the old configuration.

`envswitch` also accepts only two URLs, the account and the update one. `bmxRegistryUrl` and
`statsServerUrl` can ONLY be set by `sys configuration`, so a migration that skips those commands
never sets them at all.

The safe way is to let the service do it:

```bash
curl -X POST "http://<service-host>:8000/api/setup/migrate/<deviceId>?method=telnet&target_url=http://<service-host>:8000"
```

Use `method=telnet`. The xml method needs SSH open, and telnet is what this skill has tested. If a
speaker ends up with its runtime and persisted layers disagreeing after an in-place migration,
re-running with telnet is the documented fix.

Pauses between the commands are unnecessary: the whole sequence sent back to back lands as
completely as one spaced over seconds. When automating, wait for the `->` prompt and NOT for the
string `OK`, because `envswitch boseurls set` replies `Setting Bose Server URLs to ...` and never
says `OK`.

## Verify from the speaker, not from the service

The service cannot see what the speaker actually stored. Ask the speaker:

```bash
printf 'getpdo CurrentSystemConfiguration\r\n' | nc <speaker-ip> 17000
```

The value sits on the line AFTER its field name:

```
margeServerUrl {
  text: "http://<service-host>:8000"
}
```

Any `bose.com`, `bose.io` or `bosecm.com` still present means the migration is incomplete. So does
any `;` in a value, which means an injection was never cleaned up.

## Reboot, then wait

| Way                                                        | Needs       |
|------------------------------------------------------------|-------------|
| `uv run scripts/soundtouch_onboard.py --ip <ip> reboot --confirm` | telnet only |
| `POST /api/setup/reboot/<deviceId>?method=telnet`           | telnet only |
| `POST /api/setup/reboot/<deviceId>`                         | SSH open    |
| Unplug it                                                   | nothing     |

**The HTTP endpoint takes a method, and defaults to SSH.** Called bare on a speaker without SSH it
answers 500 without saying why, which reads as a broken service. Adding `?method=telnet` makes it
send `sys reboot` over the diagnostic port instead, so no speaker needs rooting just to be
restarted.

Do not trust the reply that it is rebooting. Confirm port 8090 actually drops, then comes back.
A wait that only checks for "back up" reports success instantly when the reboot never happened.

Timings to tell the owner:

| After a reboot | What happens              |
|----------------|---------------------------|
| about 2 s      | the speaker asks for its account |
| about 70 s     | the web API answers again |
| about 73 s     | account sync completes    |
| about 80 s     | radio sources are ready   |

Do not declare failure before roughly 80 seconds.

## If the sources never mount

All four URLs correct and still no radio means the speaker has no account bound. Without one it
never contacts the account URL at all, so this is stronger than "the sources are missing".

```bash
curl -s http://<speaker-ip>:8090/info | grep -o '<margeAccountUUID>[^<]*'
```

Empty is the cause. A speaker that was registered to the Bose cloud brings its account with it; one
that was factory reset does not. Ask the service which accounts it already knows, then bind:

`<deviceId>` is the speaker's Ethernet MAC in upper case with no separators. Read it from the
service's own device list, or from the speaker, rather than typing it out:

```bash
curl -s "http://<service-host>:8000/api/setup/devices"      # what the service knows
uv run scripts/soundtouch_find.py --ip <speaker-ip>          # or ask the speaker
```

```bash
curl -s  "http://<service-host>:8000/api/setup/account-id-suggestions/<deviceId>"
curl -X POST "http://<service-host>:8000/api/setup/pair-account/<deviceId>?account_id=<account-id>"
```

The service does the pairing by talking to the SPEAKER: it resolves the device id to an address and
tries the speaker's own HTTP call first, falling back to telnet. So the speaker has to be known to
the service and reachable from it, but it does NOT need to be pointing at the service yet. That is
why pairing works before migration, and why it fails with a 404 when the service has never seen the
device: add it by address first.

The account id is a QUERY parameter; in the body it returns 400. It does NOT have to be seven
digits. Seven digits is what the service GENERATES, so that is what most accounts look like, but the
endpoint accepts any path-safe identifier - which matters because a speaker can arrive carrying
something else entirely, and a seven-digit rule would reject it and drop the device.

Prefer this endpoint over the speaker's own `setMargeAccount`: on newer firmware that returns 404,
on some units the handler wedges, and it returns 502 when the speaker never had an account. The
firmware also wants a full acknowledged pass through its pairing exchange rather than that call on
its own.

Never call `POST /setMargeAccount` with an empty body to test. It has been observed returning 200
while silently clearing the account and unbinding a working speaker. A later retry on the same
device returned 400 and changed nothing, so this is state-dependent: not a rule you can rely on in
either direction, and a good reason not to probe with it.

**Think before putting every speaker on one account.** They may share one, as they did with Bose,
and presets are stored per device inside it. But a shared account is the condition that the
reboot-time preset wipe correlates with, and a setup using a distinct account id per speaker has not
reproduced it. If presets keep vanishing on one speaker in a multi-speaker home, that is the first
thing to change. See `presets.md`.
