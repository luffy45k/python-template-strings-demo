"""Single-request JSON interface for containerized agent execution."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from .agent import create_template_agent

MAX_REQUEST_BYTES = 1_000_000


def _domains(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _read_request(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    else:
        request_path = Path(path)
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds the 1 MB limit")
        raw = request_path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds the 1 MB limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("request must be a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one guarded agent request")
    parser.add_argument("request", nargs="?", default="-", help="JSON file or - for stdin")
    parser.add_argument("--max-steps", type=int, default=3)
    arguments = parser.parse_args(argv)

    try:
        request = _read_request(arguments.request)
        goal = request.pop("goal", None)
        if not isinstance(goal, str):
            raise TypeError("request.goal must be a string")
        agent = create_template_agent(
            max_steps=arguments.max_steps,
            allowed_domains=_domains("AGENT_ALLOWED_DOMAINS") or None,
            browser_domains=_domains("AGENT_BROWSER_DOMAINS") or None,
            name=os.getenv("AGENT_NAME", "Mrx"),
        )
        result = agent.run(goal, **request)
        print(json.dumps(_json_safe(result), ensure_ascii=False, sort_keys=True))
        return 0 if result.status == "completed" else 1
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
