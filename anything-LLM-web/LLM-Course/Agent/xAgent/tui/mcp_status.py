"""MCP 服务发现（只读适配层，兼容旧界面 / 嵌入场景）。

真正的 MCP 连接、``list_tools()`` 与工具注册由 ``core.mcp`` 完成。
本模块只在 ``XAgentApp(enable_mcp=False)``、单元测试或库嵌入使用时，
提供不启动服务的配置发现与轻量在线探测。

它做两件事：

1. 从项目配置中读取已经声明的 MCP 服务；
2. 用轻量探测判断服务当前是否可达（在线 / 离线），供右侧信息面板展示。

它不启动、不调用任何 MCP 服务，也不会修改 Agent 的工具注册表。

支持的配置位置（按优先级）：

* 环境变量 ``XAGENT_MCP_CONFIG`` 指向的 JSON 文件；
* 项目根目录 / 当前目录下的 ``mcp_servers.json``、``mcp.json``、``.mcp.json``；
* ``config.ini`` 中的 ``[mcp]`` 分区。

JSON 兼容 Claude Desktop 的 ``{"mcpServers": {...}}`` 结构，也兼容
``{"servers": [...]}`` 列表结构。
"""

from __future__ import annotations

import configparser
import json
import os
import shlex
import shutil
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from ..core.config import resolve_config_path

PROJECT_DIR = Path(__file__).resolve().parent.parent
_MCP_CONFIG_ENV = "XAGENT_MCP_CONFIG"
_MCP_JSON_ENV = "XAGENT_MCP_SERVERS"
_JSON_FILENAMES = ("mcp_servers.json", "mcp.json", ".mcp.json")
_PROBE_TIMEOUT = 0.35


@dataclass(frozen=True)
class McpServer:
    """一个 MCP 服务的展示信息。"""

    name: str
    description: str
    transport: str
    endpoint: str
    online: bool
    detail: str = ""
    source: str = ""
    tools: tuple[str, ...] = ()

    @property
    def status_text(self) -> str:
        return "在线" if self.online else "离线"


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _command_endpoint(command: str, args: Iterable[str]) -> str:
    parts = [command, *[str(arg) for arg in args]]
    text = " ".join(part for part in parts if part)
    return text or ""


def _command_available(command: str) -> tuple[bool, str]:
    if not command:
        return False, "未配置启动命令"
    if "${" in command:
        return False, "启动命令包含未解析变量"

    candidate = Path(command).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if candidate.is_file():
            return True, "命令文件存在"
        return False, f"找不到命令文件：{candidate}"

    found = shutil.which(command)
    if found:
        return True, f"可执行文件：{found}"
    return False, f"PATH 中找不到命令：{command}"


def _url_available(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 格式无效"

    host = parsed.hostname
    if not host:
        return False, "URL 缺少主机名"
    scheme = (parsed.scheme or "").lower()
    if scheme in ("https", "wss"):
        port = parsed.port or 443
    elif scheme in ("http", "ws"):
        port = parsed.port or 80
    else:
        port = parsed.port or 80

    # socket.create_connection() 的 timeout 不覆盖 DNS 解析；在 Windows 上
    # 断网时 getaddrinfo 可能阻塞数十秒。把探测放到 daemon 线程里，并只等待
    # 一个很短的固定时间，避免影响 TUI 启动/退出。
    result: list[tuple[bool, str]] = []

    def probe() -> None:
        try:
            with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT):
                result.append((True, f"端口可达：{host}:{port}"))
        except OSError as exc:
            result.append(
                (False, f"无法连接 {host}:{port}（{exc.__class__.__name__}）")
            )

    worker = threading.Thread(
        target=probe,
        name=f"xagent-mcp-probe-{host}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=_PROBE_TIMEOUT + 0.1)
    if worker.is_alive():
        return False, f"连接探测超时：{host}:{port}"
    return result[0] if result else (False, f"无法探测 {host}:{port}")


def _build_server(
    name: str,
    config: Mapping[str, Any],
    *,
    source: str,
) -> McpServer:
    description = _clean_text(
        config.get("description") or config.get("desc") or config.get("title"),
        "未提供描述",
    )
    command = _clean_text(config.get("command"))
    raw_args = config.get("args") or config.get("arguments") or ()
    if isinstance(raw_args, str):
        try:
            args = shlex.split(raw_args)
        except ValueError:
            args = raw_args.split()
    elif isinstance(raw_args, Iterable):
        args = [str(arg) for arg in raw_args]
    else:
        args = []

    url = _clean_text(
        config.get("url")
        or config.get("endpoint")
        or config.get("serverUrl")
        or config.get("server_url")
    )
    transport = _clean_text(
        config.get("transport") or config.get("type"),
        "stdio" if command else ("http" if url else "unknown"),
    ).lower()
    endpoint = _command_endpoint(command, args) if command else url

    enabled = config.get("enabled", not bool(config.get("disabled", False)))
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off", "disabled")

    explicit_status = _clean_text(config.get("status")).lower()
    if not enabled:
        online, detail = False, "已在配置中禁用"
    elif explicit_status:
        online = explicit_status in ("online", "ok", "connected", "running", "up")
        detail = f"配置声明状态：{explicit_status}"
    elif command:
        online, detail = _command_available(command)
    elif url:
        online, detail = _url_available(url)
    else:
        online, detail = False, "缺少 command 或 url"

    return McpServer(
        name=name,
        description=description,
        transport=transport,
        endpoint=endpoint,
        online=online,
        detail=detail,
        source=source,
    )


def _servers_from_json(data: Any, source: str) -> list[McpServer]:
    if not isinstance(data, Mapping):
        return []

    entries: Any = None
    for key in ("mcpServers", "mcp_servers", "servers"):
        if key in data:
            entries = data.get(key)
            break
    if entries is None and all(isinstance(value, Mapping) for value in data.values()):
        # 允许直接写成 {"server-name": {...}} 的简写形式。
        entries = data
    if entries is None:
        return []

    servers: list[McpServer] = []
    if isinstance(entries, Mapping):
        for name, config in entries.items():
            if isinstance(config, Mapping):
                servers.append(_build_server(str(name), config, source=source))
    elif isinstance(entries, list):
        for index, config in enumerate(entries):
            if not isinstance(config, Mapping):
                continue
            name = _clean_text(config.get("name") or config.get("id"), f"server-{index + 1}")
            servers.append(_build_server(name, config, source=source))
    return servers


def _split_names(raw: str) -> list[str]:
    return [part for part in raw.replace(",", " ").split() if part]


def _servers_from_ini(config_path: Path) -> list[McpServer]:
    if not config_path.is_file():
        return []

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, configparser.Error):
        return []

    if not parser.has_section("mcp"):
        return []

    section = parser["mcp"]
    names = _split_names(section.get("servers", ""))
    if not names:
        prefixes = ("mcp:", "mcp.", "mcp/")
        names = [
            item[len(prefix):]
            for item in parser.sections()
            for prefix in prefixes
            if item.startswith(prefix)
        ]

    servers: list[McpServer] = []
    for name in dict.fromkeys(names):
        config: dict[str, Any] = {}
        for candidate in (f"mcp:{name}", f"mcp.{name}", f"mcp/{name}"):
            if parser.has_section(candidate):
                config = dict(parser.items(candidate))
                break
        if not config:
            # 允许在 [mcp] 内使用 ``name.command`` 这类扁平写法。
            prefix = f"{name}."
            config = {
                key[len(prefix):]: value
                for key, value in section.items()
                if key.startswith(prefix)
            }
        if not config:
            config = {"description": "仅在 [mcp] 中声明，缺少服务配置"}
        servers.append(_build_server(name, config, source=str(config_path)))
    return servers


def _json_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get(_MCP_CONFIG_ENV)
    if env_path:
        candidates.append(Path(env_path).expanduser())

    for base in (Path.cwd(), PROJECT_DIR, resolve_config_path().parent):
        for filename in _JSON_FILENAMES:
            candidates.append(base / filename)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path.absolute())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def discover_mcp_servers() -> list[McpServer]:
    """读取 MCP 配置并探测在线状态。

    该函数会做少量网络/文件系统探测，建议放到后台线程执行。
    """
    servers: list[McpServer] = []

    # 1) 环境变量直接提供 JSON。
    raw_env = os.environ.get(_MCP_JSON_ENV)
    if raw_env:
        try:
            servers.extend(_servers_from_json(json.loads(raw_env), _MCP_JSON_ENV))
        except (TypeError, ValueError):
            pass

    # 2) JSON 配置文件（取第一个存在的文件）；与 ini 同时存在时做合并去重。
    for path in _json_candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers.extend(_servers_from_json(data, str(path)))
        break

    # 3) config.ini 的 [mcp] 分区。
    servers.extend(_servers_from_ini(resolve_config_path()))

    unique: dict[str, McpServer] = {}
    for server in servers:
        unique.setdefault(server.name, server)
    return sorted(unique.values(), key=lambda item: item.name.lower())


__all__ = ["McpServer", "discover_mcp_servers"]
