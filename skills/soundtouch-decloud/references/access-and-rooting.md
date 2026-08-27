# Getting access to a speaker

Everything here is done over the network, with the scripts in this skill. There is no USB step.

**Port 17000 is the diagnostic port.** It needs no credentials and speaks a small command language
ending each reply with a `->` prompt. Everything the migration needs can be done here, and most
owners never need more.

It is open on most models, but not a guarantee: some hardened firmware builds do not expose it, and
on a vanilla ST10 running 27.0.6 a probe found `envswitch` itself missing from the command table
while other units on the same firmware family accept it. Treat a missing `envswitch` as a
recoverable preflight result, not a broken speaker, and say so rather than retrying.

**Port 22 is SSH, and is closed on a stock speaker.** It is only needed for the service endpoints
that act on the speaker directly. Opening it is optional. Do not do it to satisfy a checklist.

## Check what is already open

```bash
uv run scripts/soundtouch_find.py --ip <speaker-ip>
```

Never probe a port with the `echo > /dev/tcp/host/port` shell redirect. It is a bash builtin, and
under `sh` (which is dash on Debian and Ubuntu, and is what `docker exec` and `ssh host 'cmd'` often
give you) it fails for every port, so a wide-open port reports as closed and nothing says why.

## Opening SSH

```bash
uv run scripts/soundtouch_onboard.py --ip <speaker-ip> \
    --service http://<service-host>:8000 enable-ssh --confirm
```

It checks the precondition below first, refuses if it is not met, reports what it would run without
`--confirm`, and tells you the next step afterwards.

Then run the migration, always. The method leaves shell text in a live configuration value and only
this takes it out:

```bash
uv run scripts/soundtouch_onboard.py --ip <speaker-ip> \
    --service http://<service-host>:8000 migrate --confirm
```

### The precondition that makes it silently do nothing

A genuinely unpaired speaker - factory reset, empty `margeAccountUUID` - **does not poll
`margeServerUrl` at all.** Pointing a reset device's marge URL at a listener recorded zero requests
over ten minutes. The method works by riding a value the speaker reads, so on an unpaired device
there is no read cycle for it to fire on: nothing happens, and nothing says so.

`enable-ssh` refuses in that case rather than appearing to succeed. Bind an account first, per the
account section of `migration.md`, then run it again.

### What it actually sends, and the two forms

The firmware passes the URL value to a shell, so a suffix appended to it runs on the speaker:

```
;touch /tmp/remote_services;/etc/init.d/sshd start
```

The **default form** writes that through the persistence layer alone. It is the field-confirmed
one, reported working on the Wireless Link Adapter and the CineMate 520 `lisa` variant:

```
envswitch boseurls set "http://<service-host>:8000;touch /tmp/remote_services;/etc/init.d/sshd start" "http://<service-host>:8000/updates/soundtouch"
```

**The injection fires when the speaker READS the value, and on some units that is only at the next
boot.** Measured on a SoundTouch 20 on 27.0.6.46330: the command replied OK, port 22 stayed refused,
and `state` showed all four URLs clean - because `envswitch boseurls set` writes the STORED
configuration while `state` reads `getpdo CurrentSystemConfiguration`, which is the RUNTIME one.
They are two different values until a boot copies one onto the other, so `getpdo` cannot confirm
this write before a reboot. After one, the runtime `margeServerUrl` carried the injection,
`/tmp/remote_services` existed and `sshd` was running.

A refused port 22 straight after the write is therefore not a failure and not grounds for the fuller
form. `enable-ssh` reports it as a question left unanswered (exit 2) and tells you to run
`reboot --confirm` and look again.

Only if port 22 is still refused after that reboot does the speaker need the fuller form, which also
puts the injection on the runtime `sys configuration` key and reboots by itself. Both differences
appear to matter:

```bash
uv run scripts/soundtouch_onboard.py --ip <speaker-ip> \
    --service http://<service-host>:8000 enable-ssh --full-config --confirm
```

It is reported on the SoundTouch Portable (Series I) and some CineMate 520 units, and upstream
records the automation of it as candidate behaviour still awaiting confirmation on hardware. Some
ST10 and CineMate 520 units never start `sshd` over telnet at all and need the serial or U-Boot
route, which is outside this skill.

Pauses between the commands are unnecessary. A controlled A/B across three variants found identical
outcomes at zero and five second gaps, which retracted an earlier theory that the gap mattered.

### Older firmware

`remote_services on` over port 17000 was the old way in. It was **removed in firmware 7.x** and is
absent through the 8.x-14.x era as well, so on anything recent it is a dead path rather than a first
thing to try. Firmware 27.x is what this migration targets, and `sys configuration` and `envswitch`
are confirmed working there on ST10, ST20, ST300, Wave III and Wave IV.

## Making it survive a reboot

The method only leaves the marker in `/tmp`, which is cleared on every boot. Skipping this fails
SILENTLY: everything works until the next power cut, and then the speaker looks as though the whole
procedure never happened.

On the speaker, the root filesystem may need remounting first, and `/etc` is the preferred location:

```sh
(touch /etc/remote_services 2>/dev/null || (mount -o remount,rw / && touch /etc/remote_services))
touch /mnt/nv/remote_services
```

Prove it took by rebooting and checking that the `/tmp` marker is gone while a persistent one
remains:

```sh
for f in /etc/remote_services /mnt/nv/remote_services /tmp/remote_services; do
    [ -e "$f" ] && echo "YES $f" || echo "no  $f"
done
```

Only `/tmp` present means access disappears at the next boot.

## Logging in

The firmware only offers old host key algorithms, so a current SSH client refuses it by default:

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<speaker-ip>
```

User `root`, no password. Tell the owner plainly: anyone on their network can log into the speaker
as root once SSH is open. That is the reason to leave it closed unless something needs it, and the
reason to close it again when the work is done - remove the persisted markers and reboot.

A factory reset does NOT close it. Reset clears the account, the presets, the four URLs and the
name, but the persisted markers survive and SSH and telnet stay open. Recovery from a reset is
re-migrate, re-pair, rename and restore presets, with no need to re-run the SSH-enable method.

## The upstream CLI

AfterTouch also ships `soundtouch-cli`, which covers similar ground (`setup enable-ssh`,
`setup migrate`, `setup remote-services`, `setup inspect`, `preset store`, `account pair`) and has
options this skill does not wrap, notably `--authorized-key` to install a key instead of relying on
the empty-password login, and `--close-17000` to firewall off the diagnostic port. Worth knowing it
exists, and worth reading when a device does something the scripts here do not cover. The procedures
in this skill are the ones tested here, and are what its scripts implement.
