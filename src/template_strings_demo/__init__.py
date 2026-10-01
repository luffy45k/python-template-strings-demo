"""Practical PEP 750 processors with compatibility for Python 3.10+."""

__version__ = "1.2.0"

from .addressing import AddressMonitor, AddressSnapshot, discover_addresses
from .agent import (
    Action,
    AgentMemory,
    AgentResult,
    AutonomousAgent,
    Finish,
    TemplatePlanner,
    ToolRegistry,
    create_template_agent,
    default_tool_registry,
)
from .browser import BrowserEvent, BrowserPolicyError, BrowserResult, BrowserSandbox
from .charts import ChartError, ChartResult, generate_chart
from .chat import ChatMessage, ChatReply, ChatSession, MrxChatBot
from .engine import (
    FailFirstAttempt,
    RenderError,
    RenderReport,
    TemplateEngine,
    parameterize_sql,
    render,
    render_html,
)
from .model import NATIVE_T_STRINGS, Interpolation, Template, build_template, template
from .network import HttpResponse, NetworkPolicyError, SafeHttpClient
from .repair import (
    CodeRepairManager,
    FileRepair,
    RepairError,
    RepairProposal,
    RepairResult,
)

__all__ = [
    "__version__",
    "Action",
    "AddressMonitor",
    "AddressSnapshot",
    "AgentMemory",
    "AgentResult",
    "AutonomousAgent",
    "BrowserEvent",
    "BrowserPolicyError",
    "BrowserResult",
    "BrowserSandbox",
    "ChartError",
    "ChartResult",
    "ChatMessage",
    "ChatReply",
    "ChatSession",
    "CodeRepairManager",
    "FailFirstAttempt",
    "FileRepair",
    "Finish",
    "HttpResponse",
    "Interpolation",
    "MrxChatBot",
    "NATIVE_T_STRINGS",
    "NetworkPolicyError",
    "RepairError",
    "RepairProposal",
    "RepairResult",
    "RenderError",
    "RenderReport",
    "SafeHttpClient",
    "Template",
    "TemplateEngine",
    "TemplatePlanner",
    "ToolRegistry",
    "build_template",
    "create_template_agent",
    "default_tool_registry",
    "discover_addresses",
    "generate_chart",
    "parameterize_sql",
    "render",
    "render_html",
    "template",
]
