from __future__ import annotations

import json

from template_strings_demo.health import check_health, main


def test_offline_health_check_and_cli(capsys):
    report = check_health()
    assert report.status == "healthy"
    assert report.agent == "Mrx"
    assert all(report.checks.values())

    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "healthy"
    assert output["checks"] == {"agent": True, "tools": True}
