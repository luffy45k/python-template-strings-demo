"""Fast offline health check for containers and process supervisors."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from .agent import create_template_agent


@dataclass(frozen=True)
class HealthReport:
    status: str
    agent: str
    checks: dict[str, bool]


def check_health() -> HealthReport:
    checks: dict[str, bool] = {}
    try:
        agent = create_template_agent(max_steps=2)
        result = agent.run("Render text", source="health={state}", values={"state": "ok"})
        checks["agent"] = result.status == "completed" and result.answer == "health=ok"
        checks["tools"] = {
            "generate_chart",
            "render_text",
            "render_html",
            "parameterize_sql",
        }.issubset(agent.tools.names())
        name = result.agent
    except Exception:
        checks["agent"] = False
        checks["tools"] = False
        name = "Mrx"
    status = "healthy" if all(checks.values()) else "unhealthy"
    return HealthReport(status, name, checks)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv:
        raise SystemExit("tstrings-health accepts no arguments")
    report = check_health()
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.status == "healthy" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
