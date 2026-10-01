"""Small standard-library web chat UI for Mrx."""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Sequence
from urllib.parse import parse_qs, urlsplit

from .addressing import AddressMonitor, discover_addresses
from .chat import MrxChatBot
from .health import check_health

MAX_BODY = 1_000_000
INDEX = b"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Mrx Chat</title><style>body{font:16px system-ui;background:#101820;color:#dce6f2;max-width:900px;margin:auto;padding:24px}#chat{min-height:260px}.m{padding:10px;margin:8px;border-radius:8px;background:#1c2b36}.u{background:#173f5f}input,textarea,button{font:inherit;padding:10px;margin:4px;background:#1c2b36;color:#fff;border:1px solid #567;border-radius:6px}input{width:65%}button{cursor:pointer}svg{max-width:100%;height:auto}</style></head>
<body><h1>Mrx</h1><p><a href=/demo>Open fictional test website</a></p><div id=chat></div><form id=f><input id=m autocomplete=off placeholder='Ask Mrx or say: create chart'><button>Send</button></form><details><summary>Optional JSON inputs</summary><textarea id=i rows=7 cols=70>{}</textarea></details><script>
const chat=document.querySelector('#chat'),sid=localStorage.mrxSid||(localStorage.mrxSid=crypto.randomUUID());
function line(text,cls){const d=document.createElement('div');d.className='m '+cls;d.textContent=text;chat.append(d)}
document.querySelector('#f').onsubmit=async e=>{e.preventDefault();const m=document.querySelector('#m');if(!m.value.trim())return;line(m.value,'u');let inputs={};try{inputs=JSON.parse(document.querySelector('#i').value)}catch{line('Invalid inputs JSON','');return}const r=await fetch('/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session:sid,message:m.value,inputs})});const out=await r.json();line(out.text||out.error,'');if(out.chart){const box=document.createElement('div');box.className='m';box.innerHTML=out.chart.svg;chat.append(box)}m.value=''};
</script></body></html>"""

DEMO_SITE = b"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Northstar Test Shop</title><style>body{font:16px system-ui;max-width:900px;margin:auto;padding:30px;background:#f5f7fb;color:#17233c}header{display:flex;justify-content:space-between;align-items:center}.hero,.card{background:white;padding:24px;margin:18px 0;border-radius:12px;box-shadow:0 3px 15px #ccd3df}input,button{font:inherit;padding:10px;border:1px solid #789;border-radius:6px}button{background:#2356d8;color:white}nav a{margin:8px}</style></head>
<body><header><strong data-testid=brand>Northstar Test Shop</strong><nav><a href=/demo>Home</a><a href=#catalog>Catalog</a></nav></header><main><section class=hero><h1>Safe fictional website fixture</h1><p>This local page exists only for Mrx browser and webpage testing.</p><form action=/demo/result method=get><label for=query>Search products</label> <input id=query name=q data-testid=search required><button data-testid=submit>Search</button></form></section><section id=catalog class=card><h2>Catalog</h2><ul><li>Orbit Notebook &mdash; $8</li><li>Nova Pen &mdash; $3</li><li>Comet Mug &mdash; $12</li></ul></section></main></body></html>"""


class ChatStore:
    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self._sessions: OrderedDict[str, MrxChatBot] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> MrxChatBot:
        try:
            uuid.UUID(session_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("session must be a UUID") from exc
        with self._lock:
            bot = self._sessions.pop(session_id, None) or MrxChatBot()
            self._sessions[session_id] = bot
            while len(self._sessions) > self.capacity:
                self._sessions.popitem(last=False)
            return bot


def create_server(host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    store = ChatStore()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Mrx/1.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", INDEX)
            elif path == "/demo":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", DEMO_SITE)
            elif path == "/demo/result":
                query = parse_qs(parsed.query).get("q", [""])[0][:200]
                safe_query = escape(query)
                body = (
                    "<!doctype html><html><head><meta charset=utf-8>"
                    "<title>Search result</title></head><body>"
                    "<main><h1>Northstar Test Shop</h1>"
                    f"<p data-testid=result>Search completed for: {safe_query}</p>"
                    '<a href="/demo">Back</a></main></body></html>'
                ).encode()
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)
            elif path == "/api/health":
                self._json(HTTPStatus.OK, asdict(check_health()))
            elif path == "/api/address":
                snapshot = discover_addresses()
                self._json(
                    HTTPStatus.OK,
                    {
                        **asdict(snapshot),
                        "urls": snapshot.urls(self.server.server_port),
                    },
                )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/chat":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("request body size is invalid")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("content-type must be application/json")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request must be an object")
                inputs = payload.get("inputs", {})
                if not isinstance(inputs, dict):
                    raise ValueError("inputs must be an object")
                bot = store.get(payload.get("session"))
                reply = bot.chat(payload.get("message"), **inputs)
                self._json(HTTPStatus.OK, asdict(reply))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )

        def _json(self, status: HTTPStatus, value) -> None:  # noqa: ANN001
            self._send(status, "application/json", json.dumps(value).encode())

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Mrx web chat")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address, or 'auto' for all interfaces",
    )
    parser.add_argument("--port", type=int, default=8080, help="use 0 for an automatic free port")
    args = parser.parse_args(argv)
    bind_host = "0.0.0.0" if args.host == "auto" else args.host
    server = create_server(bind_host, args.port)

    def announce(snapshot) -> None:  # noqa: ANN001
        print("Mrx address changed: " + ", ".join(snapshot.urls(server.server_port)))

    monitor = AddressMonitor(announce)
    monitor.start()
    if bind_host in {"0.0.0.0", "::"}:
        announce(monitor.snapshot)
    else:
        print(f"Mrx chat listening on http://{bind_host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
