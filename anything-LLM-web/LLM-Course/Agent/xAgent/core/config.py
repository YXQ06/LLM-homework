"""配置读取。

配置文件默认位于项目根目录（``xAgent/config.ini``），也可以通过
``--config`` 参数或环境变量 ``XAGENT_CONFIG`` 指定。本模块不依赖任何 UI。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.ini"
CONFIG_ENV_VAR = "XAGENT_CONFIG"
DEFAULT_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI 兼容接口的连接参数。"""

    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 300.0
    temperature: float = 0.2

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass(frozen=True)
class AgentConfig:
    """Agent 行为参数（工具轮次、读取上限等）。"""

    max_tool_rounds: int = 8
    max_list_entries: int = 200
    max_read_bytes: int = 200_000
    max_tool_result_chars: int = 12_000
    web_timeout_seconds: int = 20
    cli_timeout_seconds: int = 30
    cli_max_output_chars: int = 6_000


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
    """确定配置文件路径：显式参数 > 环境变量 > 项目默认路径。"""
    explicit = path or os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


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


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """读取 ini 配置并返回强类型对象。"""
    config_path = resolve_config_path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path}")

    parser = configparser.ConfigParser()
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
    )

    agent_section = parser["agent"] if parser.has_section("agent") else configparser.SectionProxy(parser, "agent")
    agent = AgentConfig(
        max_tool_rounds=_get_int(agent_section, "max_tool_rounds", 8, minimum=1),
        max_list_entries=_get_int(agent_section, "max_list_entries", 200, minimum=1),
        max_read_bytes=_get_int(agent_section, "max_read_bytes", 200_000, minimum=1),
        max_tool_result_chars=_get_int(agent_section, "max_tool_result_chars", 12_000, minimum=100),
        web_timeout_seconds=_get_int(agent_section, "web_timeout_seconds", 20, minimum=1),
        cli_timeout_seconds=_get_int(agent_section, "cli_timeout_seconds", 30, minimum=1),
        cli_max_output_chars=_get_int(agent_section, "cli_max_output_chars", 6_000, minimum=100),
    )

    search_section = parser["search"] if parser.has_section("search") else configparser.SectionProxy(parser, "search")
    search = SearchConfig(
        api_key=(search_section.get("baidu_qianfan_api_key") or search_section.get("api_key") or "").strip(),
        endpoint=(search_section.get("endpoint") or DEFAULT_SEARCH_ENDPOINT).strip() or DEFAULT_SEARCH_ENDPOINT,
        timeout=_get_float(search_section, "timeout", 30.0, minimum=1.0),
    )

    return AppConfig(llm=llm, agent=agent, search=search)
