#!/usr/bin/env python3
"""Ask the router's DHCP server whether an address lies outside its pool, without logging in.

    sudo python3 scripts/soundtouch_dhcp.py probe --address 192.0.2.10 --dev eth0

The service machine's fixed address must lie OUTSIDE the router's DHCP range: an address inside it
works for weeks and then collides when the router hands it to another device. The router's own
DHCP page answers that directly; this answers it when there is no login, or a wrong password would
lock the router's admin page.

It sends one DHCPDISCOVER per NEIGHBOUR of the address (one below, one above), each from a throwaway
MAC with the neighbour as the requested address. A server offers a requested address only when it
lies in its pool, so an offer of the neighbour itself means the range reaches it. Both neighbours
are asked because one alone misjudges an address at the pool's end, and the address itself is
never asked: a server skips an address that is already in use, so its answer would read "outside"
whenever the service machine already holds it. No DHCPREQUEST follows, so nothing is leased. It
also checks over ARP whether something already answers on the address. A server asked for an
address off its own network offers from its pool instead, which alone would read as "outside", so
the subnet mask in its offers decides that case: the address must lie in the network they name.

Linux only (it binds to one interface), and as root (DHCP clients use port 68). Prints a JSON
envelope: exit 0 outside the pool and free, 1 inside the pool, already in use or on another
network, 2 no DHCP answer or the probe could not run.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import random
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = ["MAGIC_COOKIE", "MAX_OFFERS", "DhcpOffer", "DhcpTransport", "PoolVerdict", "UdpTransport",
           "build_discover", "build_parser", "discover", "judge", "main", "neighbour_in_use",
           "neighbours", "parse_offer", "run"]

MAGIC_COOKIE = bytes([99, 130, 83, 99])
# A LAN has one or two DHCP servers; the cap keeps a misbehaving one from growing the reply list.
MAX_OFFERS = 8
DHCP_OFFER = 2
FIXED_HEADER = 236
SERVER_PORT, CLIENT_PORT = 67, 68
SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


class PoolVerdict(StrEnum):
    """What the probe concluded about the address."""

    OUTSIDE = "outside"
    INSIDE = "inside"
    IN_USE = "in-use"
    NO_ANSWER = "no-answer"
    OTHER_SUBNET = "other-subnet"


EXIT_CODES = {PoolVerdict.OUTSIDE: 0, PoolVerdict.INSIDE: 1, PoolVerdict.IN_USE: 1,
              PoolVerdict.NO_ANSWER: 2, PoolVerdict.OTHER_SUBNET: 1}

ADVICE = {
    PoolVerdict.OUTSIDE: ("{address} is outside the router's DHCP range and nothing answers on it, so "
                          "it is safe to give the service machine as a fixed address."),
    PoolVerdict.INSIDE: ("{address} lies inside the router's DHCP range, so the router can hand it to "
                         "another device later. Pick an address outside the range, or reserve this "
                         "one for the service machine in the router."),
    PoolVerdict.IN_USE: ("Something already answers on {address}: another device, or the service "
                         "machine itself if it already has it. Pick another address unless it is the "
                         "service machine."),
    PoolVerdict.OTHER_SUBNET: ("{address} is not on the network this DHCP server hands out "
                               "addresses in ({network}), so the speakers could not reach it. "
                               "Pick an address in {network}."),
    PoolVerdict.NO_ANSWER: ("No DHCP server answered on {dev}. Check the interface name and that "
                            "this machine is on the speakers' network, or read the range from the "
                            "router's DHCP page."),
}


@dataclass(frozen=True)
class DhcpOffer:
    """One DHCPOFFER: the address offered and what the server said about the network."""

    yiaddr: str
    server_id: str | None
    router: str | None
    dns: tuple[str, ...]
    lease_time: int | None
    netmask: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"offered": self.yiaddr, "server": self.server_id, "router": self.router,
                "dns": list(self.dns), "lease_time": self.lease_time, "netmask": self.netmask}

    def network(self) -> ipaddress.IPv4Network | None:
        """The network the offer belongs to, when the server named its mask (option 1)."""
        if self.netmask is None:
            return None
        try:
            return ipaddress.IPv4Network(f"{self.yiaddr}/{self.netmask}", strict=False)
        except ValueError:
            return None


class DhcpTransport(Protocol):
    """Where DISCOVERs go and offers come from: a UDP broadcast socket, or a stand-in in tests."""

    def send(self, packet: bytes) -> None: ...

    def receive(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


class UdpTransport:
    """The real thing: a broadcast socket bound to one interface on the DHCP client port."""

    def __init__(self, dev: str) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, dev.encode() + b"\0")
            # SO_REUSEADDR lets this sit beside a DHCP client that already holds port 68.
            self._sock.bind(("", CLIENT_PORT))
        except OSError:
            self._sock.close()
            raise

    def send(self, packet: bytes) -> None:
        self._sock.sendto(packet, ("255.255.255.255", SERVER_PORT))

    def receive(self, timeout: float) -> bytes | None:
        self._sock.settimeout(max(timeout, 0.01))
        try:
            data, _ = self._sock.recvfrom(2048)
        except TimeoutError:
            return None
        return data

    def close(self) -> None:
        self._sock.close()


def build_discover(xid: int, mac: bytes, requested: str) -> bytes:
    """A DHCPDISCOVER asking for `requested` (option 50), broadcast flag set."""
    header = (bytes([1, 1, 6, 0]) + xid.to_bytes(4, "big") + bytes(2) + (0x8000).to_bytes(2, "big")
              + bytes(16) + mac + bytes(10) + bytes(64) + bytes(128))
    options = (MAGIC_COOKIE + bytes([53, 1, 1]) + bytes([50, 4]) + socket.inet_aton(requested)
               + bytes([55, 3, 1, 3, 6]) + bytes([255]))
    return header + options


def _read_options(data: bytes) -> dict[int, bytes] | None:
    """The options after the magic cookie, or None when one runs past the end of the packet."""
    found: dict[int, bytes] = {}
    i = FIXED_HEADER + len(MAGIC_COOKIE)
    while i < len(data):
        code = data[i]
        if code == 255:
            return found
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data) or i + 2 + data[i + 1] > len(data):
            return None
        found[code] = data[i + 2:i + 2 + data[i + 1]]
        i += 2 + data[i + 1]
    return found


def _addresses(raw: bytes | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(socket.inet_ntoa(raw[j:j + 4]) for j in range(0, len(raw) - 3, 4))


def parse_offer(data: bytes, xid: int) -> DhcpOffer | None:
    """Our own DHCPOFFER read into a record; None for anything else, including a damaged packet."""
    if len(data) < FIXED_HEADER + len(MAGIC_COOKIE) or int.from_bytes(data[4:8], "big") != xid:
        return None
    if data[FIXED_HEADER:FIXED_HEADER + len(MAGIC_COOKIE)] != MAGIC_COOKIE:
        return None
    options = _read_options(data)
    if options is None or options.get(53) != bytes([DHCP_OFFER]):
        return None
    server, router = _addresses(options.get(54)), _addresses(options.get(3))
    mask, lease = _addresses(options.get(1)), options.get(51)
    return DhcpOffer(yiaddr=socket.inet_ntoa(data[16:20]), server_id=server[0] if server else None,
                     router=router[0] if router else None, dns=_addresses(options.get(6)),
                     lease_time=int.from_bytes(lease, "big") if lease and len(lease) == 4 else None,
                     netmask=mask[0] if mask else None)


def neighbours(address: str) -> tuple[str, ...]:
    """The addresses next to `address` that can be hosts, assuming the usual /24 house network."""
    ip = ipaddress.IPv4Address(address)  # raises ValueError for anything that is not IPv4
    last = int(ip) & 0xFF
    if last in (0, 255):
        raise ValueError(f"{address} is a network or broadcast address, not a host")
    return tuple(str(ip + step) for step in (-1, 1) if 0 < last + step < 255)


def discover(transport: DhcpTransport, requested: str, *, xid: int, mac: bytes,
             timeout: float = 4.0) -> tuple[DhcpOffer, ...]:
    """Send one DISCOVER for `requested` and collect the offers that answer it, at most MAX_OFFERS."""
    transport.send(build_discover(xid, mac, requested))
    offers: list[DhcpOffer] = []
    deadline = time.monotonic() + timeout
    while len(offers) < MAX_OFFERS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        data = transport.receive(remaining)
        if data is None:
            break
        offer = parse_offer(data, xid)
        if offer is not None:
            offers.append(offer)
    return tuple(offers)


def served_networks(asked: Mapping[str, Sequence[DhcpOffer]]) -> tuple[ipaddress.IPv4Network, ...]:
    """The networks the answering servers hand out addresses in, as their offers name them."""
    found = {net for offers in asked.values() for offer in offers if (net := offer.network())}
    return tuple(sorted(found))


def judge(asked: Mapping[str, Sequence[DhcpOffer]], *, address: str, in_use: bool) -> PoolVerdict:
    """The verdict from the offers each neighbour got and whether the address itself is taken.

    A server offers from its own pool when asked for an address outside its network, so neither
    neighbour comes back and the offers alone would read as "outside the range". The mask the
    offers carry tells that case apart; an offer without one cannot, and is judged as before.
    """
    if not any(asked.values()):
        return PoolVerdict.NO_ANSWER
    networks = served_networks(asked)
    if networks and not any(ipaddress.IPv4Address(address) in net for net in networks):
        return PoolVerdict.OTHER_SUBNET
    if any(offer.yiaddr == neighbour for neighbour, offers in asked.items() for offer in offers):
        return PoolVerdict.INSIDE
    return PoolVerdict.IN_USE if in_use else PoolVerdict.OUTSIDE


def neighbour_in_use(entry: str) -> bool:
    """Did something answer ARP for the address? `ip neigh` shows its MAC (lladdr) when it did.

    Any state with a MAC counts, STALE included: calling a taken address free is the costly mistake,
    calling a free one taken only costs picking another.
    """
    return "lladdr" in entry


def _run_command(cmd: Sequence[str]) -> str:
    return subprocess.run(list(cmd), capture_output=True, encoding="utf-8", errors="replace",
                          check=False).stdout


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main so the documented usage lines can be parsed in a test."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    probe = sub.add_parser("probe", help="is this address outside the DHCP pool, and free?")
    probe.add_argument("--address", required=True, help="the fixed address you want to use")
    probe.add_argument("--dev", required=True, help="the network interface on the speakers' LAN")
    return parser


def _emit(ok: bool, data: dict[str, object], code: int) -> int:
    print(json.dumps({"ok": ok, "command": "probe", "data": data}, indent=2))
    return code


def _probe(address: str, dev: str, transport: DhcpTransport,
           runner: Callable[[Sequence[str]], str]) -> int:
    asked = {neighbour: discover(transport, neighbour, xid=random.getrandbits(32),
                                 mac=bytes([0x02, 0, 0]) + os.urandom(3))
             for neighbour in neighbours(address)}
    runner(["ping", "-c", "2", "-W", "1", address])  # makes the kernel ask ARP for the address
    entry = runner(["ip", "-4", "neigh", "show", "to", address, "dev", dev]).strip()
    verdict = judge(asked, address=address, in_use=neighbour_in_use(entry))
    network = ", ".join(str(net) for net in served_networks(asked)) or None
    data: dict[str, object] = {
        "address": address, "dev": dev,
        "asked": {neighbour: [offer.to_json() for offer in offers] for neighbour, offers in asked.items()},
        "neighbour_entry": entry or None, "network": network, "verdict": verdict.value,
        "advice": ADVICE[verdict].format(address=address, dev=dev, network=network)}
    return _emit(verdict == PoolVerdict.OUTSIDE, data, EXIT_CODES[verdict])


def run(argv: list[str] | None = None, *, platform: str = sys.platform,
        open_transport: Callable[[str], DhcpTransport] = UdpTransport,
        runner: Callable[[Sequence[str]], str] = _run_command) -> int:
    """main with its three outside dependencies as parameters: the OS, the socket, the commands."""
    args = build_parser().parse_args(argv)
    address, dev = str(args.address), str(args.dev)
    if not platform.startswith("linux"):
        return _emit(False, {"error": "this probe runs on Linux only; read the DHCP range from the "
                                      "router's own DHCP page instead"}, 2)
    try:
        neighbours(address)
    except ValueError as exc:
        return _emit(False, {"error": f"{address}: {exc}"}, 2)
    try:
        transport = open_transport(dev)
    except PermissionError as exc:
        return _emit(False, {"error": f"run it as root (sudo): DHCP clients use port 68 ({exc})"}, 2)
    except OSError as exc:
        return _emit(False, {"error": f"cannot open a DHCP socket on {dev}: {exc}"}, 2)
    with contextlib.closing(transport):
        try:
            return _probe(address, dev, transport, runner)
        except OSError as exc:
            return _emit(False, {"error": f"probe failed: {exc}"}, 2)


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
