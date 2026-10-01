from __future__ import annotations

import json
from http.client import HTTPConnection
from threading import Thread
from uuid import uuid4

import pytest

from template_strings_demo import ChartError, ChatSession, MrxChatBot, generate_chart
from template_strings_demo.web import create_server


def test_bar_line_and_pie_charts_are_safe_svg():
    bar = generate_chart(["<script>", "B"], [10, -2], title="Sales <2026>")
    line = generate_chart(["Jan", "Feb"], [1, 3], kind="line")
    pie = generate_chart(["A", "B"], [2, 3], kind="pie")

    assert bar.kind == "bar"
    assert "&lt;script&gt;" in bar.svg
    assert "<script>" not in bar.svg
    assert "polyline" in line.svg
    assert "<path" in pie.svg
    assert pie.points == 2


def test_chart_input_limits():
    with pytest.raises(ChartError, match="equal length"):
        generate_chart(["A"], [1, 2])
    with pytest.raises(ChartError, match="kind"):
        generate_chart(["A"], [1], kind="3d")
    with pytest.raises(ChartError, match="positive total"):
        generate_chart(["A"], [0], kind="pie")
    with pytest.raises(ChartError, match="100 points"):
        generate_chart([str(i) for i in range(101)], list(range(101)))


def test_chatbot_conversation_chart_and_memory():
    session = ChatSession(capacity=4)
    bot = MrxChatBot(session=session)

    assert bot.chat("Hello").status == "completed"
    reply = bot.chat(
        "Create a line chart",
        labels=["Mon", "Tue"],
        values=[2, 5],
        kind="line",
        title="Traffic",
    )
    assert reply.chart is not None
    assert reply.chart.kind == "line"
    assert len(session.history()) == 4
    session.clear()
    assert session.history() == ()


def test_web_chat_health_and_chart_api():
    server = create_server("127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/")
        home = connection.getresponse()
        assert home.status == 200
        assert b"Mrx Chat" in home.read()
        assert home.getheader("X-Frame-Options") == "DENY"

        connection.request("GET", "/demo")
        demo = connection.getresponse()
        assert demo.status == 200
        demo_body = demo.read()
        assert b"Northstar Test Shop" in demo_body
        assert b"data-testid=search" in demo_body

        connection.request("GET", "/demo/result?q=%3Cscript%3Ealert(1)%3C/script%3E")
        result_page = connection.getresponse()
        result_body = result_page.read()
        assert result_page.status == 200
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in result_body
        assert b"<script>alert(1)</script>" not in result_body

        connection.request("GET", "/api/health")
        health = connection.getresponse()
        assert health.status == 200
        assert json.loads(health.read())["status"] == "healthy"

        connection.request("GET", "/api/address")
        address = connection.getresponse()
        address_payload = json.loads(address.read())
        assert address.status == 200
        assert address_payload["urls"]

        payload = json.dumps(
            {
                "session": str(uuid4()),
                "message": "Create pie chart",
                "inputs": {
                    "labels": ["A", "B"],
                    "values": [4, 6],
                    "kind": "pie",
                },
            }
        )
        connection.request(
            "POST",
            "/api/chat",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        output = json.loads(response.read())
        assert response.status == 200
        assert output["status"] == "completed"
        assert output["chart"]["kind"] == "pie"
        assert output["chart"]["svg"].startswith("<svg")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
