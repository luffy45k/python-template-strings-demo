from __future__ import annotations

from template_strings_demo import AddressMonitor, AddressSnapshot, discover_addresses


def test_address_discovery_filters_loopback_and_formats_ipv6():
    def resolver(*args, **kwargs):
        return [
            (2, 1, 6, "", ("127.0.0.1", 0)),
            (2, 1, 6, "", ("192.168.1.8", 0)),
            (10, 1, 6, "", ("2001:db8::8", 0, 0, 0)),
        ]

    snapshot = discover_addresses(resolver)
    assert snapshot.addresses == ("192.168.1.8", "2001:db8::8")
    assert snapshot.urls(8080) == (
        "http://192.168.1.8:8080",
        "http://[2001:db8::8]:8080",
    )


def test_address_monitor_reports_changes_once():
    values = iter(
        [
            AddressSnapshot("device", ("10.0.0.2",)),
            AddressSnapshot("device", ("10.0.0.3",)),
            AddressSnapshot("device", ("10.0.0.3",)),
        ]
    )
    changes = []
    monitor = AddressMonitor(changes.append, interval=1, discover=lambda: next(values))

    assert monitor.check_now().addresses == ("10.0.0.3",)
    assert monitor.check_now().addresses == ("10.0.0.3",)
    assert len(changes) == 1
