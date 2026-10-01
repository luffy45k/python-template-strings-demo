from __future__ import annotations

import pytest

from template_strings_demo import (
    BrowserPolicyError,
    BrowserSandbox,
    create_template_agent,
)


def public_resolver(host, port, type):  # noqa: A002
    return [(2, type, 6, "", ("93.184.216.34", port))]


class Route:
    def __init__(self):
        self.outcome = None

    def abort(self, reason):
        self.outcome = ("abort", reason)

    def continue_(self):
        self.outcome = ("continue",)


class Request:
    def __init__(self, url):
        self.url = url


def sandbox(**kwargs):
    return BrowserSandbox(
        ["app.example.com"],
        resolver=public_resolver,
        **kwargs,
    )


def test_browser_validates_navigation_and_form_actions():
    value = sandbox()
    navigate = value._validate_action(
        {"action": "navigate", "url": "https://app.example.com/login"}
    )
    fill = value._validate_action(
        {"action": "fill", "selector": "#email", "value": "a@example.com"}
    )

    assert navigate["action"] == "navigate"
    assert fill["selector"] == "#email"
    with pytest.raises(BrowserPolicyError, match="allowlisted"):
        value._validate_action({"action": "navigate", "url": "https://evil.example/login"})
    with pytest.raises(BrowserPolicyError, match="unsupported"):
        value._validate_action({"action": "upload", "selector": "input"})
    with pytest.raises(BrowserPolicyError, match="selector"):
        value._validate_action({"action": "click", "selector": ""})


def test_request_routing_blocks_non_allowlisted_resources():
    value = sandbox()
    allowed = Route()
    blocked = Route()

    value._route_request(allowed, Request("https://app.example.com/app.js"))
    value._route_request(blocked, Request("https://tracker.example/script.js"))

    assert allowed.outcome == ("continue",)
    assert blocked.outcome == ("abort", "blockedbyclient")


def test_action_screenshot_and_wait_limits_precede_browser_launch():
    value = sandbox(max_actions=1, allow_screenshots=False, timeout_ms=100)
    with pytest.raises(BrowserPolicyError, match="action count"):
        value.run([{"action": "wait"}, {"action": "wait"}])
    with pytest.raises(BrowserPolicyError, match="screenshots"):
        value.run([], screenshot=True)
    with pytest.raises(BrowserPolicyError, match="wait duration"):
        value._validate_action({"action": "wait", "milliseconds": 101})


def test_browser_configuration_and_tool_input_validation():
    with pytest.raises(ValueError, match="max_actions"):
        sandbox(max_actions=0)
    with pytest.raises(ValueError, match="timeout"):
        sandbox(timeout_ms=0)
    with pytest.raises(ValueError, match="extraction"):
        sandbox(max_text_chars=0)

    value = sandbox()
    with pytest.raises(TypeError, match="actions"):
        value.tool({"actions": "navigate"})
    with pytest.raises(TypeError, match="screenshot"):
        value.tool({"actions": [], "screenshot": "yes"})


def test_browser_tool_is_opt_in_and_selected_by_planner():
    offline = create_template_agent().run("Browse a page", actions=[])
    assert "not allowed" in offline.answer["error"]

    online = create_template_agent(browser_domains=("app.example.com",))
    assert "browser" in online.tools.names()
    decision = online.planner.plan(
        type(
            "Context",
            (),
            {
                "goal": "Click through a form",
                "inputs": {"actions": [{"action": "wait"}], "screenshot": True},
                "observations": (),
            },
        )()
    )
    assert decision.tool == "browser"
    assert decision.arguments["screenshot"] is True
