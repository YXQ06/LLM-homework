"""core 对外公共 API。

所有界面（TUI / Web / Desktop）都应该从这里导入所需类型和类，
core 内部模块的实现细节可以随时调整，不影响上层。
"""

from .agent import Agent
from .config import AgentConfig, AppConfig, LLMConfig, SearchConfig, load_config, resolve_config_path
from .errors import (
    AgentError,
    BusyError,
    ConfigError,
    InvalidPromptError,
    LLMConnectionError,
    LLMError,
    LLMHTTPError,
    LLMResponseError,
    LLMTimeoutError,
    ToolError,
)
from .events import (
    AgentEvent,
    AnswerReady,
    ArgumentRepaired,
    NudgeIssued,
    ToolCallFinished,
    ToolCallStarted,
    ToolRun,
    TurnFailed,
    TurnFinished,
    TurnResult,
    TurnStarted,
    empty_result,
)
from .llm.client import ChatClient, OpenAICompatibleClient
from .prompts import build_system_prompt
from .tools import (
    CliToolbox,
    SearchToolbox,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    WebToolbox,
    build_default_registry,
)

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentError",
    "AgentEvent",
    "AnswerReady",
    "AppConfig",
    "SearchConfig",
    "ArgumentRepaired",
    "BusyError",
    "ChatClient",
    "CliToolbox",
    "ConfigError",
    "InvalidPromptError",
    "LLMConfig",
    "LLMConnectionError",
    "LLMError",
    "LLMHTTPError",
    "LLMResponseError",
    "LLMTimeoutError",
    "NudgeIssued",
    "OpenAICompatibleClient",
    "SearchToolbox",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolRun",
    "ToolSpec",
    "TurnFailed",
    "TurnFinished",
    "TurnResult",
    "TurnStarted",
    "WebToolbox",
    "build_default_registry",
    "build_system_prompt",
    "empty_result",
    "load_config",
    "resolve_config_path",
]
