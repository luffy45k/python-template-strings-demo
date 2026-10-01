from __future__ import annotations

import pytest

from template_strings_demo import (
    Action,
    AgentMemory,
    AutonomousAgent,
    Finish,
    ToolRegistry,
    create_template_agent,
    default_tool_registry,
)


def test_agent_selects_html_tool_and_finishes():
    agent = create_template_agent()
    result = agent.run(
        "Escape this as HTML",
        source="<p>{name}</p>",
        values={"name": "<Admin>"},
    )

    assert result.status == "completed"
    assert result.agent == "Mrx"
    assert result.answer == "<p>&lt;Admin&gt;</p>"
    assert result.steps == 1
    assert result.observations[0].action.tool == "render_html"


def test_agent_selects_chart_tool():
    result = create_template_agent().run(
        "Create bar chart",
        labels=["A", "B"],
        values=[1, 2],
        title="Example",
    )
    assert result.answer.kind == "bar"
    assert result.answer.points == 2
    assert result.observations[0].action.tool == "generate_chart"


def test_agent_selects_parameterized_sql():
    attack = "x' OR 1=1 --"
    result = create_template_agent().run(
        "Build a safe SQL query",
        source="SELECT * FROM users WHERE name = {name}",
        values={"name": attack},
    )

    assert result.answer == ("SELECT * FROM users WHERE name = ?", (attack,))
    assert attack not in result.answer[0]


def test_disallowed_tool_is_never_called():
    called = False

    def dangerous(_):
        nonlocal called
        called = True

    class Planner:
        def plan(self, context):
            if context.observations:
                return Finish("stopped")
            return Action("dangerous")

    tools = ToolRegistry()
    tools.register("safe", lambda _: "safe")
    tools.register("dangerous", dangerous)
    result = AutonomousAgent(Planner(), tools, allowed_tools=("safe",)).run("test")

    assert result.status == "completed"
    assert result.observations[0].succeeded is False
    assert "not allowed" in result.observations[0].output
    assert called is False


def test_step_budget_stops_runaway_planner():
    class RunawayPlanner:
        def plan(self, context):
            return Action("echo", {"step": len(context.observations)})

    tools = ToolRegistry()
    tools.register("echo", lambda arguments: arguments["step"])
    result = AutonomousAgent(RunawayPlanner(), tools, max_steps=2).run("loop")

    assert result.status == "step_limit"
    assert result.answer is None
    assert result.steps == 2
    assert len(result.observations) == 2


def test_tool_failures_are_observed_and_memory_is_bounded():
    memory = AgentMemory(capacity=1)
    agent = AutonomousAgent(
        planner=create_template_agent().planner,
        tools=default_tool_registry(),
        memory=memory,
    )
    first = agent.run("render", source=None)
    agent.run("render", source="Hello")

    assert first.answer == {"error": "TypeError: source must be a string"}
    assert len(memory.snapshot()) == 1
    assert "status=completed" in memory.snapshot()[0]
    memory.clear()
    assert memory.snapshot() == ()


def test_retry_safe_tool_recovers_but_side_effect_tool_does_not_retry():
    attempts = {"safe": 0, "unsafe": 0}

    def flaky(arguments):
        attempts[arguments["kind"]] += 1
        if attempts[arguments["kind"]] == 1:
            raise RuntimeError("transient")
        return "recovered"

    class OneActionPlanner:
        def __init__(self, tool):
            self.tool = tool

        def plan(self, context):
            if context.observations:
                return Finish(context.observations[-1].output)
            return Action(self.tool, {"kind": self.tool})

    tools = ToolRegistry()
    tools.register("safe", flaky)
    tools.register("unsafe", flaky)
    safe = AutonomousAgent(
        OneActionPlanner("safe"),
        tools,
        tool_retries=1,
        retry_safe_tools=("safe",),
    ).run("recover")
    unsafe = AutonomousAgent(
        OneActionPlanner("unsafe"),
        tools,
        tool_retries=1,
        retry_safe_tools=("safe",),
    ).run("do not replay")

    assert safe.answer == "recovered"
    assert safe.recoveries == 1
    assert safe.observations[0].attempts == 2
    assert "transient" in unsafe.answer
    assert unsafe.recoveries == 0
    assert attempts == {"safe": 2, "unsafe": 1}


def test_registry_and_configuration_validation():
    tools = ToolRegistry()
    tools.register("echo", lambda arguments: arguments)
    with pytest.raises(ValueError, match="already registered"):
        tools.register("echo", lambda arguments: arguments)
    with pytest.raises(ValueError, match="tool names"):
        tools.register("not-valid!", lambda arguments: arguments)
    with pytest.raises(KeyError, match="unknown tool"):
        tools.call("missing", {})
    with pytest.raises(ValueError, match="unknown tools"):
        AutonomousAgent(create_template_agent().planner, tools, allowed_tools=("missing",))
    with pytest.raises(ValueError, match="positive"):
        AgentMemory(0)
    with pytest.raises(ValueError, match="positive"):
        AutonomousAgent(create_template_agent().planner, tools, max_steps=0)
    with pytest.raises(ValueError, match="must not be empty"):
        AutonomousAgent(create_template_agent().planner, tools).run(" ")
    with pytest.raises(ValueError, match="name"):
        AutonomousAgent(create_template_agent().planner, tools, name=" ")
    with pytest.raises(ValueError, match="tool_retries"):
        AutonomousAgent(create_template_agent().planner, tools, tool_retries=-1)
    with pytest.raises(ValueError, match="retry_safe_tools"):
        AutonomousAgent(
            create_template_agent().planner,
            tools,
            retry_safe_tools=("missing",),
        )
