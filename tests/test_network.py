from __future__ import annotations

from email.message import Message

import pytest

from template_strings_demo import NetworkPolicyError, SafeHttpClient, create_template_agent


def public_resolver(host, port, type):  # noqa: A002
    return [(2, type, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, type):  # noqa: A002
    return [(2, type, 6, "", ("127.0.0.1", port))]


class FakeResponse:
    def __init__(self, body=b'{"ok": true}', content_type="application/json"):
        self.status = 200
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, amount):
        return self.body[:amount]

    def geturl(self):
        return "https://api.example.com/data"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def client(response=None, **kwargs):
    value = SafeHttpClient(
        ["api.example.com"],
        resolver=public_resolver,
        **kwargs,
    )
    value._opener = FakeOpener(response or FakeResponse())
    return value


def test_allowed_https_get_is_bounded_and_auditable():
    value = client(timeout=2)
    response = value.get("https://api.example.com/data?q=python")

    assert response.status == 200
    assert response.content_type == "application/json"
    assert response.text == '{"ok": true}'
    assert value._opener.request.get_method() == "GET"
    assert value._opener.timeout == 2


def test_scheme_domain_credentials_and_port_are_restricted():
    value = client()
    rejected = (
        "http://api.example.com/data",
        "https://other.example.com/data",
        "https://user:pass@api.example.com/data",
        "https://api.example.com:8443/data",
    )
    for url in rejected:
        with pytest.raises(NetworkPolicyError):
            value.get(url)


def test_private_dns_destinations_are_blocked():
    value = SafeHttpClient(["localhost.example"], resolver=private_resolver)
    with pytest.raises(NetworkPolicyError, match="non-public"):
        value.get("https://localhost.example/")


def test_response_type_and_size_are_limited():
    oversized = client(FakeResponse(b"12345"), max_response_bytes=4)
    with pytest.raises(NetworkPolicyError, match="size limit"):
        oversized.get("https://api.example.com/")

    binary = client(FakeResponse(b"data", "application/octet-stream"))
    with pytest.raises(NetworkPolicyError, match="content type"):
        binary.get("https://api.example.com/")


def test_wildcard_does_not_match_root_and_input_is_validated():
    value = SafeHttpClient(["*.example.com"], resolver=public_resolver)
    assert value._validate_url("https://api.example.com") == "api.example.com"
    with pytest.raises(NetworkPolicyError, match="allowlisted"):
        value._validate_url("https://example.com")
    with pytest.raises(ValueError, match="allowed domain"):
        SafeHttpClient(["https://example.com"])
    with pytest.raises(ValueError, match="at least one"):
        SafeHttpClient([])
    with pytest.raises(ValueError, match="timeout"):
        SafeHttpClient(["example.com"], timeout=0)
    with pytest.raises(ValueError, match="max_response"):
        SafeHttpClient(["example.com"], max_response_bytes=0)


def test_agent_network_is_disabled_by_default_and_opt_in():
    offline = create_template_agent().run("Fetch this URL", url="https://api.example.com/data")
    assert "not allowed" in offline.answer["error"]

    online = create_template_agent(allowed_domains=("api.example.com",))
    assert "http_get" in online.tools.names()
