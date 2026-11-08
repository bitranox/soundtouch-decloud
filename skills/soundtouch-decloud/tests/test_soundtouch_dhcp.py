"""Tests for the DHCP pool probe: the packet it sends, the offers it reads, and what it concludes.

Every probe here goes through a fake transport handed in at the seam the real UDP socket fills, so
the suite runs on every OS without root; the socket itself is exercised on a real LAN.
"""
import json
import socket
from collections.abc import Callable, Sequence

import pytest

import soundtouch_dhcp as D
from soundtouch_core import json_object

MAC = bytes.fromhex("020000a1b2c3")
SERVER = "192.0.2.1"
POOL_ADDRESS = "192.0.2.108"


def _ip(address: str) -> bytes:
    return socket.inet_aton(address)


def _offer(xid: int, yiaddr: str, *, msg_type: int = 2, extra: bytes = b"") -> bytes:
    """A server reply as a DHCP server sends it: BOOTREPLY, the offered address in yiaddr."""
    header = (bytes([2, 1, 6, 0]) + xid.to_bytes(4, "big") + bytes(4) + bytes(4)
              + _ip(yiaddr) + _ip(SERVER) + bytes(4) + MAC + bytes(10) + bytes(64) + bytes(128))
    options = (D.MAGIC_COOKIE + bytes([53, 1, msg_type]) + bytes([54, 4]) + _ip(SERVER)
               + bytes([1, 4]) + _ip("255.255.255.0") + bytes([3, 4]) + _ip(SERVER)
               + bytes([6, 8]) + _ip(SERVER) + _ip("198.51.100.53")
               + bytes([51, 4]) + (86400).to_bytes(4, "big") + extra + bytes([255]))
    return header + options


def _options(packet: bytes) -> dict[int, bytes]:
    """The options of a packet this module built, read independently of its own parser."""
    found: dict[int, bytes] = {}
    i = 240
    while packet[i] != 255:
        found[packet[i]] = packet[i + 2:i + 2 + packet[i + 1]]
        i += 2 + packet[i + 1]
    return found


def test_a_discover_asks_for_the_requested_address() -> None:
    packet = D.build_discover(0x1234ABCD, MAC, "192.0.2.11")
    assert packet[0] == 1  # BOOTREQUEST
    assert int.from_bytes(packet[4:8], "big") == 0x1234ABCD
    assert packet[10] & 0x80  # broadcast flag: the client has no address to be answered at yet
    assert packet[28:34] == MAC
    assert packet[236:240] == D.MAGIC_COOKIE
    options = _options(packet)
    assert options[53] == bytes([1])  # DHCPDISCOVER, never a REQUEST: nothing gets leased
    assert options[50] == _ip("192.0.2.11")


def test_an_offer_is_read_into_its_fields() -> None:
    offer = D.parse_offer(_offer(7, POOL_ADDRESS), 7)
    assert offer == D.DhcpOffer(yiaddr=POOL_ADDRESS, server_id=SERVER, router=SERVER,
                                dns=(SERVER, "198.51.100.53"), lease_time=86400,
                                netmask="255.255.255.0")


@pytest.mark.parametrize(("packet", "why"), [
    (_offer(8, POOL_ADDRESS), "another client's transaction"),
    (_offer(7, POOL_ADDRESS)[:239], "shorter than the fixed header"),
    (_offer(7, POOL_ADDRESS, msg_type=5), "an ACK, not an offer"),
    (_offer(7, POOL_ADDRESS)[:-12], "an option running past the end"),
    (_offer(7, POOL_ADDRESS)[:236] + b"\x00\x00\x00\x00" + _offer(7, POOL_ADDRESS)[240:],
     "no DHCP magic cookie"),
])
def test_anything_but_our_own_offer_is_ignored(packet: bytes, why: str) -> None:
    assert D.parse_offer(packet, 7) is None, why


@pytest.mark.parametrize(("address", "expected"), [
    ("192.0.2.10", ("192.0.2.9", "192.0.2.11")),
    ("192.0.2.1", ("192.0.2.2",)),
    ("192.0.2.254", ("192.0.2.253",)),
])
def test_the_neighbours_asked_about_stay_off_network_and_broadcast(
        address: str, expected: tuple[str, ...]) -> None:
    assert D.neighbours(address) == expected


@pytest.mark.parametrize("address", ["not-an-address", "2001:db8::10", "192.0.2.0", "192.0.2.255"])
def test_an_address_that_cannot_be_a_host_is_refused(address: str) -> None:
    with pytest.raises(ValueError):
        D.neighbours(address)


def _offered(*addresses: str) -> tuple[D.DhcpOffer, ...]:
    return tuple(D.DhcpOffer(yiaddr=a, server_id=SERVER, router=SERVER, dns=(), lease_time=None)
                 for a in addresses)


@pytest.mark.parametrize(("asked", "in_use", "verdict"), [
    ({"192.0.2.9": _offered(POOL_ADDRESS), "192.0.2.11": _offered(POOL_ADDRESS)}, False,
     D.PoolVerdict.OUTSIDE),
    # The END of the pool: one neighbour is refused, the other is offered back.
    ({"192.0.2.198": _offered("192.0.2.198"), "192.0.2.200": _offered("192.0.2.150")}, False,
     D.PoolVerdict.INSIDE),
    ({"192.0.2.9": _offered(POOL_ADDRESS), "192.0.2.11": _offered(POOL_ADDRESS)}, True,
     D.PoolVerdict.IN_USE),
    ({"192.0.2.9": (), "192.0.2.11": ()}, False, D.PoolVerdict.NO_ANSWER),
])
def test_the_verdict(asked: dict[str, tuple[D.DhcpOffer, ...]], in_use: bool,
                     verdict: D.PoolVerdict) -> None:
    assert D.judge(asked, address="192.0.2.10", in_use=in_use) == verdict


def _offered_in(network_mask: str, *addresses: str) -> tuple[D.DhcpOffer, ...]:
    return tuple(D.DhcpOffer(yiaddr=a, server_id=SERVER, router=SERVER, dns=(), lease_time=None,
                             netmask=network_mask) for a in addresses)


@pytest.mark.parametrize(("address", "mask", "verdict"), [
    # The server ignores a requested address outside its network and offers from its own pool, so
    # neither neighbour is offered back: without the subnet check this read as "outside".
    ("203.0.113.10", "255.255.255.0", D.PoolVerdict.OTHER_SUBNET),
    ("192.0.2.10", "255.255.255.0", D.PoolVerdict.OUTSIDE),
    # A wider house network still contains the address.
    ("192.0.3.10", "255.255.254.0", D.PoolVerdict.OUTSIDE),
])
def test_an_address_off_the_served_network_is_named(address: str, mask: str,
                                                    verdict: D.PoolVerdict) -> None:
    asked = {"a": _offered_in(mask, POOL_ADDRESS), "b": _offered_in(mask, POOL_ADDRESS)}
    assert D.judge(asked, address=address, in_use=False) == verdict


@pytest.mark.parametrize(("entry", "in_use"), [
    ("192.0.2.10 lladdr 02:00:00:00:00:10 REACHABLE", True),
    ("192.0.2.10 lladdr 02:00:00:00:00:10 STALE", True),
    ("192.0.2.10 FAILED", False),
    ("192.0.2.10 INCOMPLETE", False),
    ("", False),
])
def test_an_address_is_in_use_when_something_answered_its_arp(entry: str, in_use: bool) -> None:
    assert D.neighbour_in_use(entry) is in_use


class FakeServer:
    """A DHCP server reached through the transport seam: it answers each DISCOVER from `answer`."""

    def __init__(self, answer: Callable[[str], Sequence[str]], *, repeat: int = 1) -> None:
        self.answer = answer
        self.repeat = repeat
        self.sent: list[bytes] = []
        self.queue: list[bytes] = []
        self.closed = False

    def send(self, packet: bytes) -> None:
        self.sent.append(packet)
        xid = int.from_bytes(packet[4:8], "big")
        requested = socket.inet_ntoa(_options(packet)[50])
        for yiaddr in self.answer(requested):
            self.queue.extend([_offer(xid, yiaddr)] * self.repeat)

    def receive(self, timeout: float) -> bytes | None:
        return self.queue.pop(0) if self.queue else None

    def close(self) -> None:
        self.closed = True


def test_discover_sends_one_request_and_collects_its_offers() -> None:
    server = FakeServer(lambda requested: [POOL_ADDRESS])
    offers = D.discover(server, "192.0.2.11", xid=99, mac=MAC, timeout=1.0)
    assert [o.yiaddr for o in offers] == [POOL_ADDRESS]
    assert len(server.sent) == 1


def test_a_flood_of_offers_is_capped() -> None:
    server = FakeServer(lambda requested: [POOL_ADDRESS], repeat=50)
    offers = D.discover(server, "192.0.2.11", xid=99, mac=MAC, timeout=1.0)
    assert len(offers) == D.MAX_OFFERS


def _run(server: FakeServer, *, entry: str = "192.0.2.10 FAILED", platform: str = "linux",
         argv: Sequence[str] = ("probe", "--address", "192.0.2.10", "--dev", "eth0"),
         capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    def runner(cmd: Sequence[str]) -> str:
        return entry if cmd[0] == "ip" else ""
    rc = D.run(list(argv), platform=platform, open_transport=lambda dev: server, runner=runner)
    return rc, json.loads(capsys.readouterr().out)


def _data(body: dict[str, object]) -> dict[str, object]:
    data = json_object(body["data"])
    assert data is not None
    return data


def test_an_address_outside_the_pool_and_free_is_a_yes(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeServer(lambda requested: [POOL_ADDRESS])
    rc, body = _run(server, capsys=capsys)
    assert rc == 0 and body["ok"] is True and body["command"] == "probe"
    assert _data(body)["verdict"] == "outside"
    assert [socket.inet_ntoa(_options(p)[50]) for p in server.sent] == ["192.0.2.9", "192.0.2.11"]
    assert server.closed


def test_an_address_inside_the_pool_is_a_no(capsys: pytest.CaptureFixture[str]) -> None:
    rc, body = _run(FakeServer(lambda requested: [requested]), capsys=capsys)
    assert rc == 1 and _data(body)["verdict"] == "inside"


def test_an_address_something_already_holds_is_a_no(capsys: pytest.CaptureFixture[str]) -> None:
    rc, body = _run(FakeServer(lambda requested: [POOL_ADDRESS]),
                    entry="192.0.2.10 lladdr 02:00:00:00:00:10 REACHABLE", capsys=capsys)
    assert rc == 1 and _data(body)["verdict"] == "in-use"


def test_an_address_on_another_network_is_a_no(capsys: pytest.CaptureFixture[str]) -> None:
    rc, body = _run(FakeServer(lambda requested: [POOL_ADDRESS]), capsys=capsys,
                    argv=("probe", "--address", "203.0.113.10", "--dev", "eth0"))
    data = _data(body)
    assert rc == 1 and body["ok"] is False and data["verdict"] == "other-subnet"
    assert data["network"] == "192.0.2.0/24"
    assert "192.0.2.0/24" in str(data["advice"])


def test_no_dhcp_answer_is_an_open_question(capsys: pytest.CaptureFixture[str]) -> None:
    rc, body = _run(FakeServer(lambda requested: []), capsys=capsys)
    assert rc == 2 and _data(body)["verdict"] == "no-answer"


def test_off_linux_it_refuses_and_points_at_the_router(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeServer(lambda requested: [POOL_ADDRESS])
    rc, body = _run(server, platform="darwin", capsys=capsys)
    assert rc == 2 and body["ok"] is False
    assert "router" in str(_data(body)["error"])
    assert server.sent == []


def test_a_bad_address_answers_with_an_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    rc, body = _run(FakeServer(lambda requested: []), capsys=capsys,
                    argv=("probe", "--address", "192.0.2.300", "--dev", "eth0"))
    assert rc == 2 and "192.0.2.300" in str(_data(body)["error"])


def test_without_root_it_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    def refused(dev: str) -> D.DhcpTransport:
        raise PermissionError(13, "Permission denied")
    rc = D.run(["probe", "--address", "192.0.2.10", "--dev", "eth0"], platform="linux",
               open_transport=refused, runner=lambda cmd: "")
    body = json.loads(capsys.readouterr().out)
    assert rc == 2 and "root" in str(_data(body)["error"])
