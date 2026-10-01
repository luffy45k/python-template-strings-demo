# Python Template Strings Demo

A small, tested PEP 750 template-processing engine with a guarded autonomous agent
named **Mrx**. It demonstrates why Python 3.14
`t"..."` strings are useful: literal text and dynamic values remain separate until a
consumer deliberately renders, escapes, or parameterizes them.

The package runs on **Python 3.10+**. On Python 3.14 it uses
`string.templatelib.Template` and `Interpolation`; earlier versions get a compatible
model and the `template()` builder.

## What is included

- f-string-equivalent rendering (conversions and format specs)
- HTML escaping of dynamic values only
- SQL parameterization—values never enter query text
- bounded retry, strict/degraded modes, and recovery metrics
- deterministic chaos injection for testing transient failures
- a bounded autonomous agent with planning, tools, memory, and an audit trail
- dependency-free SVG bar, line, and pie charts
- interactive chatbot with bounded session history and chart responses
- local web chat UI with health and JSON APIs
- command-line demo and a pytest suite

## Quick start

```bash
python -m pip install -e '.[dev]'
pytest
python -m template_strings_demo
# or: tstrings-demo
```

### Version-compatible templates

```python
from template_strings_demo import template, render, render_html

user = '<script>alert("x")</script>'
amount = 1234.567
message = template(
    "Hello {user}; balance=${amount:,.2f}",
    user=user,
    amount=amount,
)

print(render(message))
print(render_html(message))
```

On Python 3.14, native t-strings can be passed directly to the processors:

```python
from template_strings_demo import render_html

user = '<b>untrusted</b>'
print(render_html(t"<p>Hello {user}</p>"))
# <p>Hello &lt;b&gt;untrusted&lt;/b&gt;</p>
```

> Put native `t"..."` syntax only in files that require Python 3.14+. Older Python
> parsers cannot import a file containing that syntax. The `template()` builder keeps
> shared source code compatible.

## Safe SQL

```python
from template_strings_demo import parameterize_sql, template

name = "Robert'); DROP TABLE users;--"
query, parameters = parameterize_sql(
    template("SELECT * FROM users WHERE name = {name}", name=name)
)
# query      == "SELECT * FROM users WHERE name = ?"
# parameters == (name,)
cursor.execute(query, parameters)
```

Conversions and format specs are rejected for SQL interpolations because database
adapters—not string formatting—must own value serialization. A custom placeholder
such as `%s` can be supplied for other DB-API drivers.

## Self-healing and chaos testing

```python
from template_strings_demo import FailFirstAttempt, TemplateEngine, template

message = template("User {name} has {count} jobs", name="Ada", count=3)
engine = TemplateEngine(
    max_retries=1,
    chaos=FailFirstAttempt("count"),
)
report = engine.render(message)

assert report.text == "User Ada has 3 jobs"
assert report.retries == 1
assert report.recovered == 1
```

The engine retries only interpolation processors, never literal text. Strict mode
raises `RenderError` after retries are exhausted. For best-effort output use
`strict=False`; failed values become `[render-error:expression]` and are counted in
the returned `RenderReport`. Retry is bounded and exceptions such as
`KeyboardInterrupt` are never swallowed.

## Self-repair

Mrx retries only explicitly replay-safe tools (pure renderers, SQL parameterization,
and HTTPS GET) and reports successful recovery in `AgentResult.recoveries`. Browser
clicks and form submissions are never automatically replayed.

Code changes use an approval-gated transaction:

```python
from template_strings_demo import CodeRepairManager

manager = CodeRepairManager(".")
proposal = manager.propose("Correct configuration", {"config.txt": "enabled=true\n"})
print(proposal.changes[0].diff)  # review first
result = manager.apply(
    proposal,
    approval_token=proposal.approval_token,
    validator=lambda: run_project_tests(),
)
```

Repairs are limited to the configured workspace, file count, and file size. Stale
proposals are rejected, approval tokens are single-use, and every changed file is
rolled back when validation fails. The repair manager never invokes a shell itself.
Containers expose `tstrings-health`; use `--restart on-failure:3` at deployment for
process recovery. Each JSON request starts with fresh agent state.

## Autonomous agent

The optional agent layer chooses among safe template tools based on a goal. It is a
bounded agent architecture—not a claim of artificial general intelligence. The runtime,
not the planner, enforces the tool allowlist and maximum number of steps.

```python
from template_strings_demo import create_template_agent

agent = create_template_agent(max_steps=3)
result = agent.run(
    "Build a safe SQL query",
    source="SELECT * FROM users WHERE email = {email}",
    values={"email": "person@example.com"},
)

assert result.status == "completed"
assert result.answer == (
    "SELECT * FROM users WHERE email = ?",
    ("person@example.com",),
)
```

`TemplatePlanner` is deterministic and works offline. To connect an LLM later,
implement the small `Planner.plan(context) -> Action | Finish` protocol and retain the
same guarded `AutonomousAgent` runtime. Tool errors become observations, every action
is recorded, and runaway planners stop at `max_steps`.

### Opt-in network access

The default agent remains offline. HTTPS GET access is enabled only when domains are
explicitly allowlisted:

```python
agent = create_template_agent(allowed_domains=("api.example.com",))
result = agent.run("Fetch this URL", url="https://api.example.com/status")
```

`SafeHttpClient` enforces HTTPS, exact domains or explicit `*.example.com` rules,
public DNS destinations, port 443, timeouts, text content types, and a 256 KB response
limit. It rejects URL credentials and redirects, does not accept request bodies or
custom authorization headers, and records the request as an ordinary agent action.
The process or container must still permit outbound networking; keep Docker's
`--network none` option when an entirely offline run is required.

### Sandboxed web browser

Full browser automation is also opt-in and uses a fresh headless Chromium context:

```bash
python -m pip install -e '.[browser]'
playwright install chromium
```

```python
agent = create_template_agent(browser_domains=("app.example.com",))
result = agent.run(
    "Browse and submit the search form",
    actions=[
        {"action": "navigate", "url": "https://app.example.com/search"},
        {"action": "fill", "selector": "#query", "value": "PEP 750"},
        {"action": "click", "selector": "button[type=submit]"},
    ],
    screenshot=True,
)
```

Supported actions are navigation, click, fill, select, check, uncheck, key press, and
bounded wait. Every page and subresource must use HTTPS, resolve publicly, and match
the browser domain allowlist. Browser sessions are ephemeral; downloads, uploads,
popups, service workers, redirects to non-allowlisted domains, persistent profiles,
and ambient proxies are unavailable. Action count, time, extracted text, links, and
screenshots are bounded and audited. Form submission to allowlisted destinations is
supported.

A dedicated image installs Chromium while retaining a non-root runtime:

```bash
docker build -f Dockerfile.browser-sandbox -t tstrings-browser .
docker run --rm --cap-drop ALL --memory 512m --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m tstrings-browser
```

The application allowlist remains mandatory inside the container. Container egress
must be enabled for browser access; use a firewall or restricted Docker network as an
additional outer boundary in production.

### Kali Linux agent sandbox

`Dockerfile.kali-sandbox` packages Mrx and system Chromium in a minimized, non-root
Kali rolling container with an **uncompressed size target below 2 GiB**. Build-time
files, package indexes, caches, bytecode, pip, and setuptools are removed from the
runtime image. It accepts one bounded JSON request over standard input:

```bash
docker build -f Dockerfile.kali-sandbox -t tstrings-kali-agent .
./scripts/check-kali-image-size.sh tstrings-kali-agent
printf '%s' '{"goal":"Render text","source":"Hello {name}","values":{"name":"Kali"}}' | \
  docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges \
    --memory 768m --pids-limit 256 --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    tstrings-kali-agent
```

Network and browser tools remain disabled unless the operator configures allowlists:

```bash
docker run --rm -i \
  -e AGENT_ALLOWED_DOMAINS=docs.python.org,peps.python.org \
  -e AGENT_BROWSER_DOMAINS=docs.python.org,peps.python.org \
  tstrings-kali-agent < request.json
```

The request is limited to 1 MB. The container has no host socket, privileged mode,
persistent browser profile, or unrestricted agent shell. Kali is only the runtime
base; installing it does not weaken the agent's existing network and action policies.

### Termux on Android

Mrx can run natively in Termux without Docker or root. Install Termux from F-Droid or
GitHub (the old Play Store build is unsupported), clone this repository, and run:

```bash
pkg update
git clone https://github.com/luffy45k/python-template-strings-demo.git
cd python-template-strings-demo
./scripts/install-termux.sh
mrx ~/.config/mrx/example-request.json
```

Standard-input mode works well with other Termux commands:

```bash
echo '{"goal":"Render text","source":"Hello {name}","values":{"name":"Mrx"}}' | mrx -
```

Enable allowlisted HTTPS for a command or shell session:

```bash
export AGENT_ALLOWED_DOMAINS=docs.python.org,peps.python.org
mrx request.json
```

The native Termux profile includes the agent, template tools, and HTTPS client. The
Playwright/Chromium automation tool is not installed because desktop Playwright does
not officially support Android/Termux. Use the Kali or browser container on a Linux
host for automated Chromium. Termux execution is app-sandboxed by Android, but it is
not a Kali container; Mrx still has no unrestricted shell tool. Remove the runtime
with `./scripts/uninstall-termux.sh`.

## Chatbot and charts

Mrx can generate dependency-free, accessible SVG charts directly or through chat:

```python
from template_strings_demo import MrxChatBot

bot = MrxChatBot()
reply = bot.chat(
    "Create a line chart",
    labels=["Mon", "Tue", "Wed"],
    values=[12, 18, 15],
    kind="line",
    title="Requests",
)
print(reply.text)
print(reply.chart.svg)
```

Supported chart types are `bar`, `line`, and `pie`. Inputs, labels, dimensions, history,
and point count are bounded; labels and titles are escaped before entering SVG.

Run the local web chat:

```bash
tstrings-web --host 127.0.0.1 --port 8080
# Open http://127.0.0.1:8080

# Automatically select a free port and announce current LAN addresses:
tstrings-web --host auto --port 0
```

The server monitors local DHCP/interface address changes and announces updated URLs.
`/api/address` returns the current hostname, IP addresses, and URLs. This changes only
the service's local address discovery; it does not rotate public identity, bypass
network controls, or configure untrusted VPN/proxy services.

The web UI provides bounded in-memory sessions, `/api/chat`, `/api/health`, request
size limits, security headers, and no cross-origin access. Use `--host auto` only
inside a trusted network/container or behind an authenticated reverse proxy.

A fictional webpage fixture is available at `/demo`. **Northstar Test Shop** is not a
real business and does not collect credentials; it provides stable search fields,
buttons, test IDs, navigation, and escaped result output for webpage integration tests.
The pytest suite checks page loading, form-result rendering, security headers, APIs,
and script-injection escaping.

## API

| API | Purpose |
| --- | --- |
| `template(source, **values)` | Portable format-style template builder |
| `build_template(*parts)` | Construct from strings and `Interpolation` objects |
| `render(template)` | Apply `!s`/`!r`/`!a` and format specs |
| `render_html(template)` | Render and HTML-escape interpolations |
| `parameterize_sql(template)` | Return `(query, parameters)` |
| `TemplateEngine(...)` | Configurable processing, retries, and metrics |
| `FailFirstAttempt(...)` | Deterministic transient-failure injector |
| `create_template_agent()` | Ready-to-use autonomous template agent |
| `AutonomousAgent` | Guarded planner loop with step and tool limits |
| `ToolRegistry` | Explicit in-process tool allowlist |
| `AgentMemory` | Bounded run-summary memory |
| `SafeHttpClient` | Opt-in, allowlisted HTTPS GET client |
| `BrowserSandbox` | Ephemeral, allowlisted Chromium automation |
| `CodeRepairManager` | Approval-gated transactional workspace repairs |
| `tstrings-health` | Offline container/process health check |
| `generate_chart()` | Safe SVG bar, line, or pie chart |
| `MrxChatBot` | Bounded chat, tools, and chart responses |
| `tstrings-web` | Local web chat, dynamic address detection, and JSON API |
| `AddressMonitor` | Detect local DHCP/interface address changes |

## Design notes

PEP 750 evaluates expressions when a t-string is created, but delays *processing*.
This package exploits that boundary. HTML processing escapes values while preserving
trusted literal markup; SQL processing replaces each value with a driver placeholder;
and ordinary rendering intentionally reproduces f-string conversion and formatting.

The compatibility builder supports named `str.format` fields (including attribute and
item lookup), conversion flags, and nested format specs. Arbitrary Python expressions
are available only through native Python 3.14 t-string syntax.

The two `github_auto_patch_agent*.py` files are retained as historical repository
experiments and are not imported by this package.

## License

MIT
