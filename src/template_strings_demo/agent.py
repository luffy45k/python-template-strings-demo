"""A bounded autonomous workflow for choosing template-processing tools.

This is intentionally a small agent architecture, not a claim of general
intelligence. Planners decide what to do; the runtime enforces tool and step limits.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Protocol, Sequence, Union

from .browser import BrowserSandbox
from .charts import chart_tool
from .engine import parameterize_sql, render, render_html
from .model import template
from .network import SafeHttpClient

Tool = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class Action:
    """A planner request to call one registered tool."""

    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finish:
    """A planner request to stop and return an answer."""

    answer: Any


Decision = Union[Action, Finish]


@dataclass(frozen=True)
class Observation:
    """Result of one attempted action, including bounded recovery attempts."""

    step: int
    action: Action
    succeeded: bool
    output: Any
    attempts: int = 1


@dataclass(frozen=True)
class AgentContext:
    """Read-only state supplied to a planner."""

    goal: str
    inputs: Mapping[str, Any]
    observations: Sequence[Observation]
    available_tools: Sequence[str]


class Planner(Protocol):
    """Interface implemented by deterministic or AI-backed planners."""

    def plan(self, context: AgentContext) -> Decision: ...


@dataclass(frozen=True)
class AgentResult:
    """Final answer and audit trail from an agent run."""

    answer: Any
    status: str
    steps: int
    observations: Sequence[Observation]
    agent: str = "Mrx"
    recoveries: int = 0


class AgentMemory:
    """Bounded memory containing summaries, never hidden tool state."""

    def __init__(self, capacity: int = 20) -> None:
        if capacity < 1:
            raise ValueError("memory capacity must be positive")
        self._items: Deque[str] = deque(maxlen=capacity)

    def remember(self, summary: str) -> None:
        self._items.append(str(summary))

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()


class ToolRegistry:
    """Explicit allowlist of in-process tools available to an agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, name: str, tool: Tool) -> None:
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("tool names must contain only letters, numbers, and underscores")
        if name in self._tools:
            raise ValueError(f"tool {name!r} is already registered")
        self._tools[name] = tool

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc
        return tool(arguments)


class AutonomousAgent:
    """Execute planner decisions under strict, observable guardrails."""

    def __init__(
        self,
        planner: Planner,
        tools: ToolRegistry,
        *,
        max_steps: int = 5,
        allowed_tools: Optional[Sequence[str]] = None,
        memory: Optional[AgentMemory] = None,
        name: str = "Mrx",
        tool_retries: int = 0,
        retry_safe_tools: Sequence[str] = (),
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        if tool_retries < 0:
            raise ValueError("tool_retries must be non-negative")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("agent name must not be empty")
        self.name = name.strip()
        self.planner = planner
        self.tools = tools
        self.max_steps = max_steps
        self.allowed_tools = frozenset(allowed_tools or tools.names())
        unknown = self.allowed_tools.difference(tools.names())
        if unknown:
            raise ValueError(f"allowed_tools contains unknown tools: {sorted(unknown)!r}")
        unknown_retry_tools = set(retry_safe_tools).difference(tools.names())
        if unknown_retry_tools:
            raise ValueError(
                f"retry_safe_tools contains unknown tools: {sorted(unknown_retry_tools)!r}"
            )
        self.memory = memory
        self.tool_retries = tool_retries
        self.retry_safe_tools = frozenset(retry_safe_tools)

    def run(self, goal: str, **inputs: Any) -> AgentResult:
        """Pursue a goal until the planner finishes or the step budget expires."""
        if not goal.strip():
            raise ValueError("goal must not be empty")

        observations: List[Observation] = []
        available = tuple(sorted(self.allowed_tools))
        for step in range(1, self.max_steps + 1):
            context = AgentContext(goal, inputs, tuple(observations), available)
            decision = self.planner.plan(context)
            if isinstance(decision, Finish):
                result = AgentResult(
                    decision.answer,
                    "completed",
                    step - 1,
                    tuple(observations),
                    self.name,
                    sum(item.attempts - 1 for item in observations if item.succeeded),
                )
                self._remember(goal, result)
                return result
            if not isinstance(decision, Action):
                raise TypeError("planner must return Action or Finish")

            if decision.tool not in self.allowed_tools:
                observation = Observation(
                    step, decision, False, f"tool is not allowed: {decision.tool}"
                )
            else:
                max_attempts = (
                    self.tool_retries + 1 if decision.tool in self.retry_safe_tools else 1
                )
                for attempt in range(1, max_attempts + 1):
                    try:
                        output = self.tools.call(decision.tool, decision.arguments)
                        observation = Observation(step, decision, True, output, attempts=attempt)
                        break
                    except Exception as exc:  # tools are isolated extension boundaries
                        observation = Observation(
                            step,
                            decision,
                            False,
                            f"{type(exc).__name__}: {exc}",
                            attempts=attempt,
                        )
            observations.append(observation)

        result = AgentResult(
            None,
            "step_limit",
            self.max_steps,
            tuple(observations),
            self.name,
            sum(item.attempts - 1 for item in observations if item.succeeded),
        )
        self._remember(goal, result)
        return result

    def _remember(self, goal: str, result: AgentResult) -> None:
        if self.memory is not None:
            self.memory.remember(f"goal={goal!r}; status={result.status}; steps={result.steps}")


class TemplatePlanner:
    """Deterministic planner selecting a safe processor from goal keywords."""

    def plan(self, context: AgentContext) -> Decision:
        if context.observations:
            latest = context.observations[-1]
            if latest.succeeded:
                return Finish(latest.output)
            return Finish({"error": latest.output})

        goal = context.goal.lower()
        if any(word in goal for word in ("chart", "graph", "plot")):
            return Action(
                "generate_chart",
                {
                    key: context.inputs[key]
                    for key in ("labels", "values", "kind", "title", "width", "height")
                    if key in context.inputs
                },
            )
        if any(word in goal for word in ("browser", "browse", "click", "form", "page")):
            return Action(
                "browser",
                {
                    "actions": context.inputs.get("actions", ()),
                    "screenshot": context.inputs.get("screenshot", False),
                },
            )
        if any(word in goal for word in ("fetch", "http", "url", "website")):
            return Action("http_get", {"url": context.inputs.get("url")})

        source = context.inputs.get("source")
        values = context.inputs.get("values", {})
        arguments = {"source": source, "values": values}
        if "sql" in goal or "query" in goal:
            return Action("parameterize_sql", arguments)
        if "html" in goal or "escape" in goal or "xss" in goal:
            return Action("render_html", arguments)
        return Action("render_text", arguments)


def _validated_template(arguments: Mapping[str, Any]):
    source = arguments.get("source")
    values = arguments.get("values", {})
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if not isinstance(values, Mapping):
        raise TypeError("values must be a mapping")
    return template(source, **dict(values))


def default_tool_registry(
    *,
    allowed_domains: Optional[Sequence[str]] = None,
    browser_domains: Optional[Sequence[str]] = None,
) -> ToolRegistry:
    """Create built-in tools; all network capabilities require domain allowlists."""
    registry = ToolRegistry()
    registry.register("generate_chart", chart_tool)
    registry.register("render_text", lambda args: render(_validated_template(args)))
    registry.register("render_html", lambda args: render_html(_validated_template(args)))
    registry.register("parameterize_sql", lambda args: parameterize_sql(_validated_template(args)))
    if allowed_domains:
        client = SafeHttpClient(allowed_domains)
        registry.register("http_get", client.tool)
    if browser_domains:
        browser = BrowserSandbox(browser_domains)
        registry.register("browser", browser.tool)
    return registry


def create_template_agent(
    *,
    max_steps: int = 3,
    allowed_domains: Optional[Sequence[str]] = None,
    browser_domains: Optional[Sequence[str]] = None,
    name: str = "Mrx",
) -> AutonomousAgent:
    """Create Mrx with bounded recovery for side-effect-free and GET tools."""
    tools = default_tool_registry(
        allowed_domains=allowed_domains,
        browser_domains=browser_domains,
    )
    retry_safe = tuple(
        name
        for name in (
            "generate_chart",
            "render_text",
            "render_html",
            "parameterize_sql",
            "http_get",
        )
        if name in tools.names()
    )
    return AutonomousAgent(
        TemplatePlanner(),
        tools,
        max_steps=max_steps,
        name=name,
        tool_retries=1,
        retry_safe_tools=retry_safe,
    )
