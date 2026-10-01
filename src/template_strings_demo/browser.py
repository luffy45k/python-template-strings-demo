"""Opt-in ephemeral browser automation with navigation and action guardrails."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .network import NetworkPolicyError, SafeHttpClient


class BrowserPolicyError(RuntimeError):
    """Raised when a browser action violates sandbox policy."""


@dataclass(frozen=True)
class BrowserEvent:
    """Auditable outcome of one browser action."""

    action: str
    target: str
    succeeded: bool
    detail: str


@dataclass(frozen=True)
class BrowserResult:
    """Bounded data extracted from an ephemeral browser session."""

    url: str
    title: str
    text: str
    links: tuple[str, ...]
    screenshot: Optional[bytes]
    events: tuple[BrowserEvent, ...]


class BrowserSandbox:
    """Run a bounded sequence of actions in an isolated Chromium context.

    Playwright is imported only when :meth:`run` is called. Every top-level and
    subresource URL must pass the same HTTPS, domain, port, and public-DNS policy
    as :class:`SafeHttpClient`.
    """

    ACTIONS = frozenset(
        {"navigate", "click", "fill", "select", "check", "uncheck", "press", "wait"}
    )

    def __init__(
        self,
        allowed_domains: Iterable[str],
        *,
        max_actions: int = 12,
        timeout_ms: int = 8_000,
        max_text_chars: int = 100_000,
        max_links: int = 200,
        allow_screenshots: bool = True,
        resolver=None,
    ) -> None:
        if max_actions < 1:
            raise ValueError("max_actions must be positive")
        if timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")
        if max_text_chars < 1 or max_links < 0:
            raise ValueError("browser extraction limits are invalid")
        network_options = {}
        if resolver is not None:
            network_options["resolver"] = resolver
        self.network_policy = SafeHttpClient(allowed_domains, **network_options)
        self.max_actions = max_actions
        self.timeout_ms = timeout_ms
        self.max_text_chars = max_text_chars
        self.max_links = max_links
        self.allow_screenshots = allow_screenshots

    def run(
        self,
        actions: Sequence[Mapping[str, Any]],
        *,
        screenshot: bool = False,
    ) -> BrowserResult:
        """Execute validated actions in a fresh, non-persistent browser context."""
        if len(actions) > self.max_actions:
            raise BrowserPolicyError(f"action count exceeds configured limit of {self.max_actions}")
        if screenshot and not self.allow_screenshots:
            raise BrowserPolicyError("screenshots are disabled")
        validated = tuple(self._validate_action(item) for item in actions)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "browser support requires: pip install '.[browser]' && playwright install chromium"
            ) from exc

        events: list[BrowserEvent] = []
        image: Optional[bytes] = None
        with sync_playwright() as playwright:
            executable_path = os.getenv("TEMPLATE_STRINGS_BROWSER_EXECUTABLE")
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=executable_path,
                args=["--no-proxy-server"],
            )
            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
                java_script_enabled=True,
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.set_default_navigation_timeout(self.timeout_ms)
            page.route("**/*", self._route_request)
            page.on("download", lambda download: download.cancel())
            page.on("popup", lambda popup: popup.close())
            try:
                for action in validated:
                    self._execute(page, action)
                    events.append(
                        BrowserEvent(
                            action["action"],
                            action.get("url") or action.get("selector") or "",
                            True,
                            "completed",
                        )
                    )
                final_url = page.url
                if final_url and final_url != "about:blank":
                    self._validate_destination(final_url)
                title = page.title()[:2_000]
                text = page.locator("body").inner_text()[: self.max_text_chars]
                links = tuple(
                    href
                    for href in page.locator("a[href]").evaluate_all("els => els.map(e => e.href)")[
                        : self.max_links
                    ]
                    if isinstance(href, str) and len(href) <= 4_096
                )
                if screenshot:
                    image = page.screenshot(type="png", full_page=False)
                return BrowserResult(final_url, title, text, links, image, tuple(events))
            finally:
                context.close()
                browser.close()

    def tool(self, arguments: Mapping[str, Any]) -> BrowserResult:
        """ToolRegistry-compatible browser entry point."""
        actions = arguments.get("actions")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            raise TypeError("actions must be a sequence of mappings")
        screenshot = arguments.get("screenshot", False)
        if not isinstance(screenshot, bool):
            raise TypeError("screenshot must be a boolean")
        return self.run(actions, screenshot=screenshot)

    def _route_request(self, route, request) -> None:  # noqa: ANN001
        try:
            host = self.network_policy._validate_url(request.url)
            self.network_policy._validate_addresses(host)
        except (NetworkPolicyError, ValueError):
            route.abort("blockedbyclient")
            return
        route.continue_()

    def _validate_action(self, item: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            raise TypeError("each browser action must be a mapping")
        action = item.get("action")
        if action not in self.ACTIONS:
            raise BrowserPolicyError(f"unsupported browser action: {action!r}")
        result = dict(item)
        if action == "navigate":
            url = item.get("url")
            if not isinstance(url, str):
                raise TypeError("navigate action requires a string url")
            self._validate_destination(url)
        elif action == "wait":
            milliseconds = item.get("milliseconds", 0)
            if not isinstance(milliseconds, int) or not 0 <= milliseconds <= self.timeout_ms:
                raise BrowserPolicyError("wait duration is outside the allowed range")
        else:
            selector = item.get("selector")
            if not isinstance(selector, str) or not selector or len(selector) > 1_000:
                raise BrowserPolicyError("action requires a valid selector")
        return result

    def _validate_destination(self, url: str) -> None:
        try:
            host = self.network_policy._validate_url(url)
            self.network_policy._validate_addresses(host)
        except NetworkPolicyError as exc:
            raise BrowserPolicyError(str(exc)) from exc

    def _execute(self, page, action: Mapping[str, Any]) -> None:  # noqa: ANN001
        kind = action["action"]
        if kind == "navigate":
            page.goto(action["url"], wait_until="domcontentloaded")
        elif kind == "click":
            page.locator(action["selector"]).click()
        elif kind == "fill":
            value = action.get("value")
            if not isinstance(value, str) or len(value) > 10_000:
                raise BrowserPolicyError("fill value must be a bounded string")
            page.locator(action["selector"]).fill(value)
        elif kind == "select":
            value = action.get("value")
            if not isinstance(value, str):
                raise BrowserPolicyError("select value must be a string")
            page.locator(action["selector"]).select_option(value)
        elif kind == "check":
            page.locator(action["selector"]).check()
        elif kind == "uncheck":
            page.locator(action["selector"]).uncheck()
        elif kind == "press":
            key = action.get("key")
            if not isinstance(key, str) or len(key) > 50:
                raise BrowserPolicyError("press key must be a bounded string")
            page.locator(action["selector"]).press(key)
        elif kind == "wait":
            page.wait_for_timeout(action.get("milliseconds", 0))
