"""Bounded conversational interface combining Mrx tools and SVG charts."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Mapping, Optional

from .agent import AutonomousAgent, create_template_agent
from .charts import ChartResult, generate_chart
from .health import check_health


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str


@dataclass(frozen=True)
class ChatReply:
    text: str
    chart: Optional[ChartResult]
    status: str


class ChatSession:
    """In-memory bounded chat history with no hidden persistence."""

    def __init__(self, capacity: int = 40) -> None:
        if capacity < 2:
            raise ValueError("chat capacity must be at least 2")
        self._messages: Deque[ChatMessage] = deque(maxlen=capacity)

    def add(self, role: str, text: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("chat role must be user or assistant")
        self._messages.append(ChatMessage(role, str(text)[:20_000]))

    def history(self) -> tuple[ChatMessage, ...]:
        return tuple(self._messages)

    def clear(self) -> None:
        self._messages.clear()


class MrxChatBot:
    """Offline chat router for status, agent tools, and chart generation."""

    def __init__(
        self,
        agent: Optional[AutonomousAgent] = None,
        session: Optional[ChatSession] = None,
    ) -> None:
        self.agent = agent or create_template_agent()
        self.session = session or ChatSession()

    def chat(self, message: str, **inputs: Any) -> ChatReply:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must not be empty")
        message = message.strip()
        self.session.add("user", message)
        lowered = message.lower()

        if any(word in lowered for word in ("hello", "hi ", "namaste", "hey")):
            reply = ChatReply(
                "Namaste, main Mrx hoon. Task ya chart data bhejiye.", None, "completed"
            )
        elif "help" in lowered:
            reply = ChatReply(
                "I can render templates, create safe SQL/HTML, check health, "
                "browse approved sites, and generate bar, line, or pie charts.",
                None,
                "completed",
            )
        elif "health" in lowered or "status" in lowered:
            health = check_health()
            reply = ChatReply(f"Mrx is {health.status}.", None, health.status)
        elif "chart" in lowered or "graph" in lowered or "plot" in lowered:
            reply = self._chart_reply(inputs)
        elif "source" in inputs:
            result = self.agent.run(message, **inputs)
            text = str(result.answer)
            reply = ChatReply(text, None, result.status)
        else:
            reply = ChatReply(
                "Task details are incomplete. Add source/values for templates, "
                "labels/values for charts, or an approved URL/action list.",
                None,
                "needs_input",
            )

        self.session.add("assistant", reply.text)
        return reply

    @staticmethod
    def _chart_reply(inputs: Mapping[str, Any]) -> ChatReply:
        labels = inputs.get("labels")
        values = inputs.get("values")
        if labels is None or values is None:
            return ChatReply("Chart needs labels and numeric values.", None, "needs_input")
        chart = generate_chart(
            labels,
            values,
            kind=inputs.get("kind", "bar"),
            title=inputs.get("title", "Mrx Chart"),
        )
        return ChatReply(
            f"Created {chart.kind} chart with {chart.points} points.", chart, "completed"
        )
