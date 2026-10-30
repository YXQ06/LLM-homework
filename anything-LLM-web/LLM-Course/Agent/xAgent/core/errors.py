"""core 层的领域异常。

所有异常都与具体界面（TUI / Web / Desktop）无关，UI 只负责把
``AgentError`` 的 message 展示给用户。
"""

from __future__ import annotations


class AgentError(Exception):
    """xAgent 领域异常基类。"""

    kind = "error"


class ConfigError(AgentError):
    """配置文件缺失、格式错误或缺少必填项。"""

    kind = "config"


class InvalidPromptError(AgentError):
    """用户输入为空或不符合要求。"""

    kind = "invalid_prompt"


class BusyError(AgentError):
    """上一轮对话尚未结束时再次发起请求。"""

    kind = "busy"


class LLMError(AgentError):
    """大模型接口相关错误的基类。"""

    kind = "llm"


class LLMTimeoutError(LLMError):
    """请求超时。"""

    kind = "llm_timeout"


class LLMHTTPError(LLMError):
    """接口返回 4xx / 5xx。"""

    kind = "llm_http"

    def __init__(self, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.body = (body or "").strip()
        message = f"HTTP 错误 {status_code}"
        if self.body:
            message = f"{message}：{self.body[:1000]}"
        super().__init__(message)


class LLMConnectionError(LLMError):
    """无法连接接口服务（未启动、端口不通等）。"""

    kind = "llm_connection"


class LLMResponseError(LLMError):
    """接口返回的数据结构不符合预期。"""

    kind = "llm_response"


class ToolError(AgentError):
    """工具执行失败（参数错误、路径不存在等）。"""

    kind = "tool"
