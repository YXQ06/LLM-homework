"""配置读取与首次运行配置生成。

配置文件默认位于**当前工作目录**的 ``config.ini``；如果没有，会优先回退到
xAgent 源码目录下的 ``config.ini``（便于在源码目录直接运行和测试），最后
才把当前目录的路径作为目标。也可以通过 ``--config`` 参数或环境变量
``XAGENT_CONFIG`` 指定。

当用户首次在某个项目目录运行 xAgent 时，``ensure_config()`` 会生成一份带
mock 值的完整配置；界面负责提示用户修改后重新启动。本模块不依赖任何 UI。
"""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILENAME = "config.ini"
DEFAULT_CONFIG_PATH = PROJECT_DIR / DEFAULT_CONFIG_FILENAME
CONFIG_ENV_VAR = "XAGENT_CONFIG"
DEFAULT_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"

DEFAULT_CONFIG_TEMPLATE = """\
# xAgent 配置文件
# 本文件由 xAgent 首次运行时自动生成，当前填写的是 mock 信息。
# 请把 [llm] 下的 base_url、model、api_key 修改为真实可用的值，然后重新启动。
# 其余参数都有默认值，可按需调整。

[llm]
# OpenAI 兼容接口地址，例如 https://api.openai.com/v1 或本地推理服务地址
base_url = http://127.0.0.1:8000/v1
model = mock-model
api_key = sk-mock-key
timeout = 300
temperature = 0.2
# 0 表示尝试从 /models 接口探测；探测不到时使用 128k 并显示「默认」
context_window = 0

[agent]
max_tool_rounds = 8
max_list_entries = 200
max_read_bytes = 200000
max_tool_result_chars = 12000
web_timeout_seconds = 20
cli_timeout_seconds = 30
cli_max_output_chars = 6000

# 上下文长度监控与压缩
context_warn_ratio = 0.6
compact_target_tokens = 5000
context_archive_dir = .agents/context
recall_max_chars = 6000
recall_max_results = 8
# 压缩摘要输入预算：0 表示按模型窗口自动计算
compact_transcript_max_chars = 0
compact_transcript_min_chars = 20000
compact_transcript_ratio = 0.7
# 英文/数字按多少个字符估算 1 Token（中文按 1 字符 1 Token）
context_estimate_chars_per_token = 4.0

# 会话保存与恢复
session_dir = .agents/sessions
session_max_history = 50
session_auto_save_after_turn = true
session_auto_save_on_exit = true

# 项目说明文件：会话首次加载时注入一次，之后直接复用
agents_md_filename = AGENTS.md
agents_md_max_chars = 200000

# 智能体长期规则：启动时读取并放在系统提示词最前端（文件不存在则跳过）
agent_md_filename = AGENT.md
agent_md_max_chars = 200000

# 农学试验工具：代码解释器沙盒目录、分析结果目录、单次代码超时
agronomy_sandbox_dir = .agents/sandbox
agronomy_output_dir = .agents/outputs
agronomy_code_timeout = 30

# TUI
tui_sidebar_collapse_width = 110
tui_spinner_interval = 0.1
tui_command_panel_max_items = 8

# MCP 连接默认值（.mcp.json 中未写 timeout / connect_timeout 时使用）
mcp_timeout = 60
mcp_connect_timeout = 20
mcp_startup_wait_extra = 15

[search]
baidu_qianfan_api_key = mock-search-key
endpoint = https://qianfan.baidubce.com/v2/ai_search/web_search
timeout = 30

# 可选的 MCP 服务声明（通常直接使用项目目录下的 .mcp.json）
[mcp]
servers =
"""


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI 兼容接口的连接参数。"""

    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 300.0
    temperature: float = 0.2
    context_window: int = 0  # 0 表示自动探测；探测失败时使用 128k 默认值

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"


@dataclass(frozen=True)
class AgentConfig:
    """Agent 行为参数（工具轮次、读取上限、会话与界面参数等）。"""

    max_tool_rounds: int = 8
    max_list_entries: int = 200
    max_read_bytes: int = 200_000
    max_tool_result_chars: int = 12_000
    web_timeout_seconds: int = 20
    cli_timeout_seconds: int = 30
    cli_max_output_chars: int = 6_000

    # 上下文压缩相关：达到窗口的 60% 后暂停普通提问，等待 /compact。
    context_warn_ratio: float = 0.6
    compact_target_tokens: int = 5_000
    context_archive_dir: str = ".agents/context"
    recall_max_chars: int = 6_000
    recall_max_results: int = 8
    compact_transcript_max_chars: int = 0
    compact_transcript_min_chars: int = 20_000
    compact_transcript_ratio: float = 0.7
    context_estimate_chars_per_token: float = 4.0

    # 会话持久化
    session_dir: str = ".agents/sessions"
    session_max_history: int = 50
    session_auto_save_after_turn: bool = True
    session_auto_save_on_exit: bool = True

    # AGENTS.md 项目说明
    agents_md_filename: str = "AGENTS.md"
    agents_md_max_chars: int = 200_000

    # AGENT.md 长期固定规则（启动时放在系统提示词最前端）
    agent_md_filename: str = "AGENT.md"
    agent_md_max_chars: int = 200_000

    # 农学试验工具：代码解释器沙盒、分析输出目录、单次代码执行超时
    agronomy_sandbox_dir: str = ".agents/sandbox"
    agronomy_output_dir: str = ".agents/outputs"
    agronomy_code_timeout: int = 30

    # TUI 可调参数
    tui_sidebar_collapse_width: int = 110
    tui_spinner_interval: float = 0.1
    tui_command_panel_max_items: int = 8

    # MCP 默认超时（.mcp.json 未显式配置时使用）
    mcp_timeout: float = 60.0
    mcp_connect_timeout: float = 20.0
    mcp_startup_wait_extra: float = 15.0


@dataclass(frozen=True)
class SearchConfig:
    """百度千帆 AI 搜索配置。"""

    api_key: str = ""
    endpoint: str = DEFAULT_SEARCH_ENDPOINT
    timeout: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    agent: AgentConfig
    search: SearchConfig = SearchConfig()


def resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """确定配置文件路径。

    优先级：显式参数 > 环境变量 > 当前目录 ``config.ini`` > 源码目录
    ``config.ini`` > 当前目录（供首次生成使用）。
    """
    explicit = path or os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser().resolve()

    cwd_config = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_config.is_file():
        return cwd_config
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return cwd_config


def ensure_config(
    path: str | os.PathLike[str] | None = None,
) -> tuple[Path, bool]:
    """确保当前目录存在配置文件；缺失时生成完整 mock 配置。

    返回 ``(配置文件路径, 是否新建)``。只有未显式指定路径时才自动创建，
    避免 ``XAGENT_CONFIG`` 指向错误路径时悄悄生成一份无关配置。
    """
    explicit = path or os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        target = Path(explicit).expanduser().resolve()
        if not target.is_file():
            raise ConfigError(f"配置文件不存在：{target}")
        return target, False

    target = (Path.cwd() / DEFAULT_CONFIG_FILENAME).resolve()
    if target.is_file():
        return target, False

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ConfigError(f"创建配置文件失败：{target}（{exc}）") from exc
    return target, True


def _get_float(
    section: configparser.SectionProxy,
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    raw = section.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"配置项 {key} 必须是数字，当前值：{raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"配置项 {key} 不能小于 {minimum}，当前值：{value}")
    return value


def _get_int(
    section: configparser.SectionProxy,
    key: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    raw = section.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"配置项 {key} 必须是整数，当前值：{raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"配置项 {key} 不能小于 {minimum}，当前值：{value}")
    return value


def _get_bool(
    section: configparser.SectionProxy,
    key: str,
    default: bool,
) -> bool:
    raw = section.get(key)
    if raw is None or not raw.strip():
        return default
    text = raw.strip().lower()
    if text in ("1", "true", "yes", "on", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disabled"):
        return False
    raise ConfigError(f"配置项 {key} 必须是布尔值，当前值：{raw!r}")


def _get_text(
    section: configparser.SectionProxy,
    key: str,
    default: str,
) -> str:
    value = (section.get(key) or "").strip()
    return value or default


_SECRET_INVISIBLE_CHARS = (
    "\ufeff",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\u00a0",
)


def _clean_secret(value: str | None) -> str:
    """清理密钥粘贴时常见的空格、换行、引号和不可见字符。"""
    text = str(value or "")
    for char in _SECRET_INVISIBLE_CHARS:
        text = text.replace(char, "")
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return re.sub(r"\s+", "", text)


def _agent_section(
    parser: configparser.ConfigParser,
) -> configparser.SectionProxy:
    if parser.has_section("agent"):
        return parser["agent"]
    return configparser.SectionProxy(parser, "agent")


def _search_section(
    parser: configparser.ConfigParser,
) -> configparser.SectionProxy:
    if parser.has_section("search"):
        return parser["search"]
    return configparser.SectionProxy(parser, "search")


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """读取 ini 配置并返回强类型对象。"""
    config_path = resolve_config_path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path}")

    # interpolation=None：API Key 等值里可能出现 %，不应被 configparser 当作变量。
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        raise ConfigError(f"读取配置文件失败：{exc}") from exc

    if not parser.has_section("llm"):
        raise ConfigError(f"配置文件缺少 [llm] 分区：{config_path}")

    llm_section = parser["llm"]
    base_url = (llm_section.get("base_url") or "").strip()
    model = (llm_section.get("model") or "").strip()
    if not base_url:
        raise ConfigError("配置项 [llm] base_url 不能为空")
    if not model:
        raise ConfigError("配置项 [llm] model 不能为空")

    llm = LLMConfig(
        base_url=base_url,
        model=model,
        api_key=(llm_section.get("api_key") or "").strip(),
        timeout=_get_float(llm_section, "timeout", 300.0, minimum=1.0),
        temperature=_get_float(llm_section, "temperature", 0.2, minimum=0.0),
        context_window=_get_int(llm_section, "context_window", 0, minimum=0),
    )

    agent_section = _agent_section(parser)
    warn_ratio = _get_float(
        agent_section, "context_warn_ratio", 0.6, minimum=0.1
    )
    if warn_ratio > 1.0:
        raise ConfigError(
            f"配置项 context_warn_ratio 不能大于 1，当前值：{warn_ratio}"
        )
    transcript_ratio = _get_float(
        agent_section, "compact_transcript_ratio", 0.7, minimum=0.1
    )
    if transcript_ratio > 1.0:
        raise ConfigError(
            "配置项 compact_transcript_ratio 不能大于 1，"
            f"当前值：{transcript_ratio}"
        )

    agent = AgentConfig(
        max_tool_rounds=_get_int(agent_section, "max_tool_rounds", 8, minimum=1),
        max_list_entries=_get_int(agent_section, "max_list_entries", 200, minimum=1),
        max_read_bytes=_get_int(agent_section, "max_read_bytes", 200_000, minimum=1),
        max_tool_result_chars=_get_int(
            agent_section, "max_tool_result_chars", 12_000, minimum=100
        ),
        web_timeout_seconds=_get_int(
            agent_section, "web_timeout_seconds", 20, minimum=1
        ),
        cli_timeout_seconds=_get_int(
            agent_section, "cli_timeout_seconds", 30, minimum=1
        ),
        cli_max_output_chars=_get_int(
            agent_section, "cli_max_output_chars", 6_000, minimum=100
        ),
        context_warn_ratio=warn_ratio,
        compact_target_tokens=_get_int(
            agent_section, "compact_target_tokens", 5_000, minimum=500
        ),
        context_archive_dir=_get_text(
            agent_section, "context_archive_dir", ".agents/context"
        ),
        recall_max_chars=_get_int(
            agent_section, "recall_max_chars", 6_000, minimum=500
        ),
        recall_max_results=_get_int(
            agent_section, "recall_max_results", 8, minimum=1
        ),
        compact_transcript_max_chars=_get_int(
            agent_section, "compact_transcript_max_chars", 0, minimum=0
        ),
        compact_transcript_min_chars=_get_int(
            agent_section, "compact_transcript_min_chars", 20_000, minimum=1_000
        ),
        compact_transcript_ratio=transcript_ratio,
        context_estimate_chars_per_token=_get_float(
            agent_section,
            "context_estimate_chars_per_token",
            4.0,
            minimum=1.0,
        ),
        session_dir=_get_text(agent_section, "session_dir", ".agents/sessions"),
        session_max_history=_get_int(
            agent_section, "session_max_history", 50, minimum=1
        ),
        session_auto_save_after_turn=_get_bool(
            agent_section, "session_auto_save_after_turn", True
        ),
        session_auto_save_on_exit=_get_bool(
            agent_section, "session_auto_save_on_exit", True
        ),
        agents_md_filename=_get_text(
            agent_section, "agents_md_filename", "AGENTS.md"
        ),
        agents_md_max_chars=_get_int(
            agent_section, "agents_md_max_chars", 200_000, minimum=1_000
        ),
        agent_md_filename=_get_text(
            agent_section, "agent_md_filename", "AGENT.md"
        ),
        agent_md_max_chars=_get_int(
            agent_section, "agent_md_max_chars", 200_000, minimum=1_000
        ),
        agronomy_sandbox_dir=_get_text(
            agent_section, "agronomy_sandbox_dir", ".agents/sandbox"
        ),
        agronomy_output_dir=_get_text(
            agent_section, "agronomy_output_dir", ".agents/outputs"
        ),
        agronomy_code_timeout=_get_int(
            agent_section, "agronomy_code_timeout", 30, minimum=1
        ),
        tui_sidebar_collapse_width=_get_int(
            agent_section, "tui_sidebar_collapse_width", 110, minimum=40
        ),
        tui_spinner_interval=_get_float(
            agent_section, "tui_spinner_interval", 0.1, minimum=0.02
        ),
        tui_command_panel_max_items=_get_int(
            agent_section, "tui_command_panel_max_items", 8, minimum=1
        ),
        mcp_timeout=_get_float(agent_section, "mcp_timeout", 60.0, minimum=1.0),
        mcp_connect_timeout=_get_float(
            agent_section, "mcp_connect_timeout", 20.0, minimum=1.0
        ),
        mcp_startup_wait_extra=_get_float(
            agent_section, "mcp_startup_wait_extra", 15.0, minimum=0.0
        ),
    )

    search_section = _search_section(parser)
    search = SearchConfig(
        api_key=_clean_secret(
            search_section.get("baidu_qianfan_api_key")
            or search_section.get("api_key")
            or ""
        ),
        endpoint=_get_text(search_section, "endpoint", DEFAULT_SEARCH_ENDPOINT),
        timeout=_get_float(search_section, "timeout", 30.0, minimum=1.0),
    )

    return AppConfig(llm=llm, agent=agent, search=search)


__all__ = [
    "AgentConfig",
    "AppConfig",
    "CONFIG_ENV_VAR",
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_TEMPLATE",
    "DEFAULT_SEARCH_ENDPOINT",
    "LLMConfig",
    "SearchConfig",
    "ensure_config",
    "load_config",
    "resolve_config_path",
]
