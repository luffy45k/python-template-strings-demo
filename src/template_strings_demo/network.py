"""Opt-in, policy-restricted HTTPS access for autonomous agents."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NetworkPolicyError(RuntimeError):
    """Raised when a URL or response violates the network sandbox policy."""


@dataclass(frozen=True)
class HttpResponse:
    """Bounded text response returned by :class:`SafeHttpClient`."""

    url: str
    status: int
    content_type: str
    text: str


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


Resolver = Callable[..., Sequence[Any]]


class SafeHttpClient:
    """GET-only HTTPS client with allowlists and SSRF defenses.

    Network access is disabled unless at least one domain is explicitly allowed.
    Wildcards must use the form ``*.example.com`` and do not match the root domain.
    """

    def __init__(
        self,
        allowed_domains: Iterable[str],
        *,
        timeout: float = 5.0,
        max_response_bytes: int = 256_000,
        allowed_content_types: Iterable[str] = (
            "application/json",
            "text/plain",
            "text/html",
        ),
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        domains = tuple(self._normalize_rule(item) for item in allowed_domains)
        if not domains:
            raise ValueError("at least one allowed domain is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self.allowed_domains = domains
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.allowed_content_types = frozenset(
            item.lower().strip() for item in allowed_content_types
        )
        self.resolver = resolver
        # Ignore ambient proxy variables so the validated destination is the one dialed.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def get(self, url: str) -> HttpResponse:
        """Fetch one allowed HTTPS URL without following redirects."""
        host = self._validate_url(url)
        self._validate_addresses(host)
        request = Request(
            url,
            method="GET",
            headers={"User-Agent": "python-template-strings-demo/1.2"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = int(response.status)
                content_type = response.headers.get_content_type().lower()
                if content_type not in self.allowed_content_types:
                    raise NetworkPolicyError(
                        f"response content type is not allowed: {content_type}"
                    )
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise NetworkPolicyError("response exceeds configured size limit")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    text = body.decode(charset)
                except (LookupError, UnicodeDecodeError) as exc:
                    raise NetworkPolicyError("response is not valid decodable text") from exc
                return HttpResponse(response.geturl(), status, content_type, text)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise NetworkPolicyError("HTTP redirects are disabled") from exc
            raise NetworkPolicyError(f"HTTP request failed with status {exc.code}") from exc
        except URLError as exc:
            raise NetworkPolicyError(f"network request failed: {exc.reason}") from exc

    def tool(self, arguments: Mapping[str, Any]) -> HttpResponse:
        """ToolRegistry-compatible wrapper around :meth:`get`."""
        url = arguments.get("url")
        if not isinstance(url, str):
            raise TypeError("url must be a string")
        return self.get(url)

    def _validate_url(self, url: str) -> str:
        if not isinstance(url, str):
            raise TypeError("url must be a string")
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise NetworkPolicyError("only HTTPS URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise NetworkPolicyError("credentials in URLs are not allowed")
        if not parsed.hostname:
            raise NetworkPolicyError("URL must include a hostname")
        if parsed.port not in (None, 443):
            raise NetworkPolicyError("only the standard HTTPS port is allowed")
        try:
            host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise NetworkPolicyError("invalid internationalized hostname") from exc
        if not any(self._matches(host, rule) for rule in self.allowed_domains):
            raise NetworkPolicyError(f"domain is not allowlisted: {host}")
        return host

    def _validate_addresses(self, host: str) -> None:
        try:
            records = self.resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise NetworkPolicyError(f"DNS resolution failed for {host}") from exc
        if not records:
            raise NetworkPolicyError(f"DNS returned no addresses for {host}")
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                raise NetworkPolicyError(f"domain resolves to a non-public address: {address}")

    @staticmethod
    def _normalize_rule(rule: str) -> str:
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("allowed domains must be non-empty strings")
        value = rule.strip().lower().rstrip(".")
        wildcard = value.startswith("*.")
        domain = value[2:] if wildcard else value
        try:
            domain = domain.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(f"invalid allowed domain: {rule!r}") from exc
        if not domain or "*" in domain or "/" in domain or ":" in domain:
            raise ValueError(f"invalid allowed domain: {rule!r}")
        return f"*.{domain}" if wildcard else domain

    @staticmethod
    def _matches(host: str, rule: str) -> bool:
        if rule.startswith("*."):
            suffix = rule[1:]
            return host.endswith(suffix) and host != rule[2:]
        return host == rule
