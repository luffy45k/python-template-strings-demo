"""Local address discovery and change monitoring for Mrx services."""

from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class AddressSnapshot:
    hostname: str
    addresses: tuple[str, ...]

    def urls(self, port: int, scheme: str = "http") -> tuple[str, ...]:
        urls = []
        for address in self.addresses:
            host = f"[{address}]" if ":" in address else address
            urls.append(f"{scheme}://{host}:{port}")
        return tuple(urls)


Resolver = Callable[..., Sequence[tuple]]


def discover_addresses(resolver: Resolver = socket.getaddrinfo) -> AddressSnapshot:
    """Return current non-loopback interface addresses without external services."""
    hostname = socket.gethostname()
    addresses = set()
    try:
        records = resolver(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        records = ()
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        except ValueError:
            continue
        if not address.is_loopback and not address.is_unspecified:
            addresses.add(str(address))
    if not addresses:
        addresses.add("127.0.0.1")
    return AddressSnapshot(hostname, tuple(sorted(addresses)))


class AddressMonitor:
    """Poll local addresses and notify only when they change."""

    def __init__(
        self,
        callback: Callable[[AddressSnapshot], None],
        *,
        interval: float = 5.0,
        discover: Callable[[], AddressSnapshot] = discover_addresses,
    ) -> None:
        if interval < 1:
            raise ValueError("address polling interval must be at least one second")
        self.callback = callback
        self.interval = interval
        self.discover = discover
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.snapshot = discover()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mrx-address-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)

    def check_now(self) -> AddressSnapshot:
        current = self.discover()
        if current != self.snapshot:
            self.snapshot = current
            self.callback(current)
        return current

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.check_now()
