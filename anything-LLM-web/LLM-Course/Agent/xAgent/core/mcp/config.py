"""MCP Server 配置读取。

xAgent 以**程序启动时的项目目录**（``os.getcwd()``）为起点查找
``.mcp.json``，并向上逐级查找父目录；也可以通过环境变量
``XAGENT_MCP_CONFIG`` 指向任意 JSON 文件。

配置格式兼容 Claude Desktop / MCP 社区约定：

.. code-block:: json

    {
      "mcpServers": {
        "local-tools": {
          "type": "stdio",
          "command": "python",
          "args": ["server.py"],
          "env": {"TOKEN": "..."}
        },
        "remote-tools": {
          "type": "streamable_http",
          "url": "https://example.com/mcp",
          "headers": {"Authorization": "Bearer ..."}
        }
      },
      "autoStart": {
        "command": "powershell",
        "args": ["-File", "start_mcp_servers.ps1"],
        "cwd": "."
      }
    }

``autoStart`` 是可选扩展：连接前执行一次本地启动脚本，适合拉起本地
``streamable_http`` Server；脚本应幂等。

本模块只负责解析配置，不连接服务；连接失败不会影响其他 MCP Server
或本地工具。
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

MCP_CONFIG_FILENAME = ".mcp.json"
MCP_CONFIG_ENV = "XAGENT_MCP_CONFIG"

# 对外支持的传输方式。``http`` / ``streamable-http`` 统一归一化为
# ``streamable_http``。
SUPPORTED_TRANSPORTS = ("stdio", "streamable_http", "sse")

_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "streamable_http": "streamable_http",
    "streamablehttp": "streamable_http",
    "http": "streamable_http",
    "streamable": "streamable_http",
    "sse": "sse",
}

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_CONNECT_TIMEOUT = 20.0


@dataclass(frozen=True)
class McpServerConfig:
    """单个 MCP Server 的连接配置。"""

    name: str
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = _DEFAULT_TIMEOUT
    connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT
    description: str = ""
    enabled: bool = True

    @property
    def endpoint(self) -> str:
        """用于界面展示的地址/命令。"""
        if self.transport == "stdio":
            parts = [self.command, *self.args]
            return " ".join(part for part in parts if part)
        return self.url

    @property
    def transport_label(self) -> str:
        if self.transport == "streamable_http":
            return "Streamable HTTP"
        if self.transport == "sse":
            return "SSE"
        return "stdio"


@dataclass(frozen=True)
class McpAutoStart:
    """连接 MCP Server 之前自动执行的本地启动命令。

    这是 xAgent 的可选扩展：``streamable_http`` Server 需要独立进程，
    可以通过 ``autoStart`` 在 xAgent 启动时自动拉起（脚本应具备幂等性）。
    """

    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    timeout: float = 90.0

    @property
    def display(self) -> str:
        parts = [self.command, *self.args]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class McpConfig:
    """整个 ``.mcp.json`` 的解析结果。"""

    servers: tuple[McpServerConfig, ...] = ()
    path: Path | None = None
    issues: tuple[str, ...] = ()
    auto_start: McpAutoStart | None = None

    @property
    def enabled_servers(self) -> tuple[McpServerConfig, ...]:
        return tuple(server for server in self.servers if server.enabled)

    def get(self, name: str) -> McpServerConfig | None:
        for server in self.servers:
            if server.name == name:
                return server
        return None


def find_mcp_config_path(project_root: str | os.PathLike[str] | None = None) -> Path:
    """查找 ``.mcp.json``：显式环境变量 > 项目目录及其父目录。"""
    explicit = os.environ.get(MCP_CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()

    start = (
        Path(project_root).expanduser()
        if project_root is not None
        else Path.cwd()
    )
    try:
        start = start.resolve()
    except OSError:
        start = start.absolute()

    if start.is_file():
        start = start.parent

    for base in (start, *start.parents):
        candidate = base / MCP_CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return start / MCP_CONFIG_FILENAME


def _expand(value: Any) -> str:
    """展开 ``${VAR}`` / ``$VAR`` / ``%VAR%`` 环境变量。"""
    text = "" if value is None else str(value)
    return os.path.expandvars(text).strip()


def _clean_name(value: Any, fallback: str) -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def _parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in ("0", "false", "no", "off", "disabled", "disable")


def _parse_float(value: Any, default: float) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _parse_args(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            return tuple(shlex.split(text))
        except ValueError:
            return tuple(text.split())
    if isinstance(value, Iterable):
        return tuple(_expand(item) for item in value if str(item).strip())
    return ()


def _parse_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if key is None:
            continue
        result[str(key)] = _expand(item)
    return result


def _normalize_transport(raw: Any, *, has_command: bool, has_url: bool) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if not text:
        return "stdio" if has_command else ("streamable_http" if has_url else "")
    return _TRANSPORT_ALIASES.get(text, text)


def _build_server(
    index: int,
    name: Any,
    raw: Mapping[str, Any],
    *,
    base_dir: Path,
    issues: list[str],
    default_timeout: float = _DEFAULT_TIMEOUT,
    default_connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
) -> McpServerConfig | None:
    fallback_name = f"server-{index + 1}"
    server_name = _clean_name(name or raw.get("name") or raw.get("id"), fallback_name)

    command = _expand(raw.get("command"))
    url = _expand(
        raw.get("url")
        or raw.get("endpoint")
        or raw.get("serverUrl")
        or raw.get("server_url")
    )
    transport = _normalize_transport(
        raw.get("type") or raw.get("transport"),
        has_command=bool(command),
        has_url=bool(url),
    )

    if transport not in SUPPORTED_TRANSPORTS:
        issues.append(
            f"MCP Server {server_name!r} 使用了不支持的传输方式："
            f"{transport or '未指定'}（支持 stdio / streamable_http / sse）"
        )
        return None

    if transport == "stdio" and not command:
        issues.append(f"MCP Server {server_name!r} 缺少 command，已跳过。")
        return None
    if transport in ("streamable_http", "sse") and not url:
        issues.append(f"MCP Server {server_name!r} 缺少 url，已跳过。")
        return None
    if transport in ("streamable_http", "sse") and not url.lower().startswith(
        ("http://", "https://")
    ):
        issues.append(
            f"MCP Server {server_name!r} 的 url 必须以 http:// 或 https:// 开头：{url}"
        )
        return None

    timeout = _parse_float(raw.get("timeout"), default_timeout)
    connect_timeout = _parse_float(
        raw.get("connect_timeout") or raw.get("connectTimeout"),
        min(timeout, default_connect_timeout),
    )

    return McpServerConfig(
        name=server_name,
        transport=transport,
        command=command,
        args=_parse_args(raw.get("args") or raw.get("arguments")),
        env=_parse_mapping(raw.get("env") or raw.get("environment")),
        cwd=_resolve_relative(_expand(raw.get("cwd")), base_dir)
        if _expand(raw.get("cwd"))
        else "",
        url=url,
        headers=_parse_mapping(raw.get("headers")),
        timeout=timeout,
        connect_timeout=connect_timeout,
        description=_expand(raw.get("description") or raw.get("desc") or raw.get("title")),
        enabled=_parse_bool(raw.get("enabled"), not _parse_bool(raw.get("disabled"), False)),
    )


def _resolve_relative(path_text: str, base_dir: Path) -> str:
    """把配置中的相对路径按配置文件所在目录解析。"""
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _parse_auto_start(
    value: Any,
    *,
    base_dir: Path,
    issues: list[str],
) -> McpAutoStart | None:
    """解析可选的 ``autoStart`` 配置。"""
    if value is None or value is False:
        return None

    if isinstance(value, str):
        parts = _parse_args(value)
        if not parts:
            issues.append("autoStart 是空字符串，已忽略。")
            return None
        command, args = parts[0], parts[1:]
        return McpAutoStart(
            command=_expand(command),
            args=args,
            cwd=str(base_dir),
        )

    if not isinstance(value, Mapping):
        issues.append("autoStart 必须是字符串或对象，已忽略。")
        return None

    command = _expand(value.get("command") or value.get("cmd"))
    if not command:
        issues.append("autoStart 缺少 command，已忽略。")
        return None

    cwd = _expand(value.get("cwd"))
    return McpAutoStart(
        command=command,
        args=_parse_args(value.get("args") or value.get("arguments")),
        env=_parse_mapping(value.get("env") or value.get("environment")),
        cwd=_resolve_relative(cwd, base_dir) if cwd else str(base_dir),
        timeout=_parse_float(
            value.get("timeout") or value.get("startup_timeout"),
            90.0,
        ),
    )


def _server_entries(data: Mapping[str, Any]) -> tuple[Any, list[str]]:
    """从根对象中取出服务器条目，并返回 (entries, issues)。"""
    issues: list[str] = []
    for key in ("mcpServers", "mcp_servers", "servers", "mcp"):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, Mapping) and "servers" in value:
            value = value.get("servers")
        return value, issues

    # 兼容直接写成 {"server-name": {...}} 的简写形式。
    values = list(data.values())
    if values and all(isinstance(value, Mapping) for value in values):
        ignored = {"version", "settings", "defaults"}
        if not any(key in ignored for key in data):
            return data, issues
    return None, issues


def load_mcp_config(
    path: str | os.PathLike[str] | None = None,
    *,
    project_root: str | os.PathLike[str] | None = None,
    default_timeout: float = _DEFAULT_TIMEOUT,
    default_connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
) -> McpConfig:
    """读取并解析 MCP 配置；所有错误都收集到 ``issues``，不抛异常。

    ``default_timeout`` / ``default_connect_timeout`` 由 ``config.ini`` 的
    ``[agent]`` 传入，作为 ``.mcp.json`` 未显式配置时的默认值。
    """
    config_path = (
        Path(path).expanduser()
        if path is not None
        else find_mcp_config_path(project_root)
    )
    if not config_path.is_file():
        if path is not None or os.environ.get(MCP_CONFIG_ENV):
            return McpConfig(
                path=config_path,
                issues=(f"未找到 MCP 配置文件：{config_path}",),
            )
        return McpConfig(path=None)

    try:
        raw_text = config_path.read_text(encoding="utf-8-sig")
        data = json.loads(raw_text)
    except OSError as exc:
        return McpConfig(path=config_path, issues=(f"读取 MCP 配置失败：{exc}",))
    except (TypeError, ValueError) as exc:
        return McpConfig(
            path=config_path,
            issues=(f"MCP 配置文件不是合法 JSON：{config_path}（{exc}）",),
        )

    if not isinstance(data, Mapping):
        return McpConfig(
            path=config_path,
            issues=(f"MCP 配置根节点必须是 JSON 对象：{config_path}",),
        )

    entries, issues = _server_entries(data)
    if entries is None:
        return McpConfig(
            path=config_path,
            issues=(
                f"MCP 配置缺少 mcpServers 字段：{config_path}",
            ),
        )

    servers: list[McpServerConfig] = []
    seen: set[str] = set()
    if isinstance(entries, Mapping):
        items = list(entries.items())
    elif isinstance(entries, list):
        items = [
            (
                entry.get("name") or entry.get("id") if isinstance(entry, Mapping) else None,
                entry,
            )
            for entry in entries
        ]
    else:
        return McpConfig(
            path=config_path,
            issues=(f"mcpServers 必须是对象或数组：{config_path}",),
        )

    base_dir = config_path.parent
    for index, (name, raw) in enumerate(items):
        if not isinstance(raw, Mapping):
            issues.append(f"mcpServers 第 {index + 1} 项不是对象，已跳过。")
            continue
        server = _build_server(
            index,
            name,
            raw,
            base_dir=base_dir,
            issues=issues,
            default_timeout=default_timeout,
            default_connect_timeout=default_connect_timeout,
        )
        if server is None:
            continue
        if server.name in seen:
            issues.append(f"MCP Server 名称重复：{server.name!r}，后一项已跳过。")
            continue
        seen.add(server.name)
        servers.append(server)

    auto_raw = None
    for key in ("autoStart", "auto_start", "startupCommand", "startup_command"):
        if key in data:
            auto_raw = data.get(key)
            break
    auto_start = _parse_auto_start(
        auto_raw,
        base_dir=base_dir,
        issues=issues,
    )

    return McpConfig(
        servers=tuple(servers),
        path=config_path,
        issues=tuple(issues),
        auto_start=auto_start,
    )


__all__ = [
    "MCP_CONFIG_ENV",
    "MCP_CONFIG_FILENAME",
    "SUPPORTED_TRANSPORTS",
    "McpAutoStart",
    "McpConfig",
    "McpServerConfig",
    "find_mcp_config_path",
    "load_mcp_config",
]
