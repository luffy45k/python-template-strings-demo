"""Command-line demonstration."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from .agent import create_template_agent
from .engine import FailFirstAttempt, TemplateEngine, parameterize_sql, render, render_html
from .model import NATIVE_T_STRINGS, template


def run_demo() -> None:
    user = "<script>alert('nope')</script>"
    balance = 1234.567
    greeting = template("Hello {user}; balance: ${balance:,.2f}", user=user, balance=balance)
    print(f"Native PEP 750 support: {NATIVE_T_STRINGS}")
    print(f"Plain: {render(greeting)}")
    print(f"HTML:  {render_html(greeting)}")

    hostile = "Robert'); DROP TABLE users;--"
    statement = template("SELECT * FROM users WHERE name = {name}", name=hostile)
    sql, params = parameterize_sql(statement)
    print(f"SQL:   {sql}")
    print(f"Args:  {params!r}")

    chaos = TemplateEngine(max_retries=1, chaos=FailFirstAttempt("balance"))
    report = chaos.render(greeting)
    print(f"Heal:  {report.text}")
    print(f"       retries={report.retries}, recovered={report.recovered}")

    agent_result = create_template_agent().run(
        "Safely escape this HTML",
        source="<p>{user}</p>",
        values={"user": user},
    )
    print(f"{agent_result.agent}: {agent_result.answer}")
    print(f"       status={agent_result.status}, steps={agent_result.steps}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PEP 750 template string demonstration")
    parser.add_argument("command", nargs="?", choices=("demo",), default="demo")
    parser.parse_args(argv)
    run_demo()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
