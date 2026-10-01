from __future__ import annotations

import json

from template_strings_demo.agent_cli import MAX_REQUEST_BYTES, _domains, _read_request, main


def test_agent_cli_runs_json_request(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "goal": "Render text",
                "source": "Hello {name}",
                "values": {"name": "Kali"},
            }
        )
    )

    assert main([str(request)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["agent"] == "Mrx"
    assert output["answer"] == "Hello Kali"
    assert output["observations"][0]["action"]["tool"] == "render_text"


def test_agent_cli_reports_invalid_request(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text("[]")

    assert main([str(request)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert "JSON object" in output["error"]


def test_agent_cli_limits_input_and_reads_domain_configuration(tmp_path, monkeypatch):
    request = tmp_path / "large.json"
    request.write_bytes(b"x" * (MAX_REQUEST_BYTES + 1))
    try:
        _read_request(str(request))
    except ValueError as exc:
        assert "1 MB" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("oversized request was accepted")

    monkeypatch.setenv("AGENT_ALLOWED_DOMAINS", " docs.python.org,peps.python.org ")
    assert _domains("AGENT_ALLOWED_DOMAINS") == (
        "docs.python.org",
        "peps.python.org",
    )
