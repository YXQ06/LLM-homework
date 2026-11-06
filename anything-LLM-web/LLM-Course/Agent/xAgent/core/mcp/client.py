"""MCP 客户端管理器。

实现要点：

* 使用 MCP Python SDK 的标准接口 ``ClientSession`` / ``stdio_client`` /
  ``streamable_http_client``（同时兼容 1.x 的 ``streamablehttp_client`` 命名）；
* 每个 MCP Server 在后台事件循环中拥有独立的连接任务，连接生命周期
  与工具调用都在同一个任务内完成，避免 AnyIO 取消作用域跨任务退出的问题；
* 同步的 xAgent 工具线程通过线程安全队列调用异步 ``call_tool()``；
* 单个 Server 连接失败只记录错误状态，不影响其他 Server 和本地工具。
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import inspect
import logging
import os
import subprocess
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import ToolError
from .config import McpConfig, McpServerConfig
from .tools import (
    DiscoveredTool,
    McpToolRoute,
    build_mcp_tools,
    clip_result,
    format_call_result,
    require_arguments,
)

logger = logging.getLogger(__name__)


class _StdioStartupBannerFilter(logging.Filter):
    """忽略部分 stdio Server 启动时向 stdout 打印的横幅。

    MCP 协议要求 stdout 只输出 JSON-RPC；但例如 ``bazi-mcp`` 会先打印
    ``Bazi MCP server is running on stdio.``。SDK 会把它记录成一条
    ``Failed to parse JSONRPC message`` 的异常日志，连接本身不受影响。
    这里只过滤这一条已知噪音，其他协议错误仍然照常输出。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage() != "Failed to parse JSONRPC message from server":
            return True
        if not record.exc_info:
            return True
        exc = record.exc_info[1]
        return "running on stdio" not in str(exc)


def _install_stdio_banner_filter() -> None:
    transport_logger = logging.getLogger("mcp.client.stdio")
    if not any(
        isinstance(item, _StdioStartupBannerFilter)
        for item in transport_logger.filters
    ):
        transport_logger.addFilter(_StdioStartupBannerFilter())


_install_stdio_banner_filter()

# ---------------------------------------------------------------------------
# MCP SDK 兼容层
# ---------------------------------------------------------------------------

_MCP_AVAILABLE = False
_MCP_IMPORT_ERROR = ""
_SESSION_TIMEOUT_IS_FLOAT = True
_HTTP_TRANSPORT_IS_V2 = False

try:  # pragma: no cover - 取决于运行环境是否安装 mcp
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    _MCP_AVAILABLE = True
except Exception as exc:  # pragma: no cover - 依赖缺失或损坏时优雅降级
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    get_default_environment = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = str(exc)

try:  # pragma: no cover
    from mcp.client.streamable_http import streamable_http_client as _http_client_factory

    _HTTP_TRANSPORT_IS_V2 = True
except ImportError:  # pragma: no cover
    try:
        from mcp.client.streamable_http import (  # type: ignore[no-redef]
            streamablehttp_client as _http_client_factory,
        )

        _HTTP_TRANSPORT_IS_V2 = False
    except ImportError:
        _http_client_factory = None  # type: ignore[assignment]

try:  # pragma: no cover
    from mcp.client.sse import sse_client as _sse_client_factory
except ImportError:  # pragma: no cover
    _sse_client_factory = None  # type: ignore[assignment]


def _detect_mcp_major_version() -> int:
    try:
        from importlib.metadata import version

        return int(version("mcp").split(".", 1)[0])
    except Exception:  # pragma: no cover - 版本信息不可用时按 2.x 处理
        return 2


if _MCP_AVAILABLE:  # pragma: no cover
    _SESSION_TIMEOUT_IS_FLOAT = _detect_mcp_major_version() >= 2


def _timeout_arg(seconds: float) -> Any:
    """1.x 使用 timedelta，2.x 使用 float。"""
    if _SESSION_TIMEOUT_IS_FLOAT:
        return float(seconds)
    return timedelta(seconds=float(seconds))


def _describe_exception(exc: BaseException) -> str:
    nested = getattr(exc, "exceptions", None)
    if nested:
        return "；".join(_describe_exception(item) for item in nested)
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}：{text}" if text else name


def _get_field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _call_with_supported_kwargs(factory: Any, *args: Any, **kwargs: Any) -> Any:
    """只传入当前 SDK 版本支持的参数，兼容 1.x / 2.x。"""
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):  # pragma: no cover
        return factory(*args, **kwargs)

    parameters = signature.parameters
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return factory(*args, **kwargs)
    return factory(
        *args,
        **{key: value for key, value in kwargs.items() if key in parameters},
    )


def _http_timeout(connect_timeout: float, read_timeout: float) -> Any:
    """构造 httpx 超时对象；2.x 的 MCP SDK 使用 httpx2。"""
    try:
        import httpx2

        return httpx2.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )
    except Exception:  # pragma: no cover - httpx2 不可用时使用 SDK 默认值
        return None


# ---------------------------------------------------------------------------
# 数据对象
# ---------------------------------------------------------------------------


@dataclass
class McpServerStatus:
    """一个 MCP Server 的连接状态，供 CLI / TUI 展示。"""

    name: str
    transport: str
    endpoint: str
    description: str = ""
    enabled: bool = True
    connected: bool = False
    tool_count: int = 0
    tools: tuple[str, ...] = ()
    error: str = ""

    @property
    def transport_label(self) -> str:
        if self.transport == "streamable_http":
            return "Streamable HTTP"
        if self.transport == "sse":
            return "SSE"
        return "stdio"

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return "已禁用"
        return "已连接" if self.connected else "未连接"


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class McpManager:
    """连接 MCP Server、发现工具，并转发 ``call_tool()``。"""

    def __init__(
        self,
        config: McpConfig,
        *,
        max_result_chars: int = 12_000,
        startup_wait_extra: float = 15.0,
    ) -> None:
        self.config = config
        self.max_result_chars = max_result_chars
        self.startup_wait_extra = max(0.0, float(startup_wait_extra))

        self._started = False
        self._closed = False
        self._lock = threading.Lock()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._statuses: list[McpServerStatus] = []
        self._status_by_name: dict[str, McpServerStatus] = {}
        self._config_by_name: dict[str, McpServerConfig] = {}
        self._ready_events: dict[str, threading.Event] = {}
        self._discovered: list[DiscoveredTool] = []

        self._specs: tuple[Any, ...] = ()
        self._routes: dict[str, McpToolRoute] = {}
        self._reserved_names: set[str] = set()
        self._auto_start_message = ""

    # -- 对外状态 ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def specs(self) -> tuple[Any, ...]:
        return self._specs

    @property
    def routes(self) -> Mapping[str, McpToolRoute]:
        return dict(self._routes)

    @property
    def server_tools(self) -> Mapping[str, tuple[str, ...]]:
        """MCP Server -> 对外暴露的 LLM 工具名列表。"""
        grouped: dict[str, list[str]] = {}
        for route in self._routes.values():
            grouped.setdefault(route.server, []).append(route.exposed)
        return {
            server: tuple(sorted(names))
            for server, names in grouped.items()
        }

    @property
    def statuses(self) -> tuple[McpServerStatus, ...]:
        return tuple(self._statuses)

    def server_status(self, name: str) -> McpServerStatus | None:
        return self._status_by_name.get(name)

    def startup_messages(self) -> list[str]:
        """生成适合 CLI / TUI 直接展示的启动摘要。"""
        messages = [f"MCP 配置：{issue}" for issue in self.config.issues]
        if self._auto_start_message:
            messages.append(self._auto_start_message)
        if not self.config.servers:
            return messages

        for status in self._statuses:
            if not status.enabled:
                messages.append(f"MCP {status.name}：已在配置中禁用")
            elif status.connected:
                messages.append(
                    f"MCP {status.name}：已连接"
                    f"（{status.transport_label}，发现 {status.tool_count} 个工具）"
                )
            elif status.error:
                messages.append(f"MCP {status.name}：连接失败：{status.error}")
            else:
                messages.append(f"MCP {status.name}：未连接")
        return messages

    # -- 启动 / 关闭 ------------------------------------------------------

    def start(self, reserved_names: Iterable[str] = ()) -> list[Any]:
        """连接所有配置的服务并返回可注册的 ``ToolSpec``。

        该方法会阻塞到所有 Server 的连接尝试结束；单个 Server 失败只会写入
        ``statuses``，不会抛异常。
        """
        with self._lock:
            if self._started:
                return list(self._specs)
            self._started = True
            self._reserved_names = {name for name in reserved_names if name}

        self._config_by_name = {server.name: server for server in self.config.servers}
        self._statuses = [
            McpServerStatus(
                name=server.name,
                transport=server.transport,
                endpoint=server.endpoint,
                description=server.description,
                enabled=server.enabled,
            )
            for server in self.config.servers
        ]
        self._status_by_name = {status.name: status for status in self._statuses}

        if not self.config.servers:
            return []

        if not _MCP_AVAILABLE:
            error = (
                "未安装 MCP Python SDK，请先执行 "
                "`pip install mcp`（"
                + (_MCP_IMPORT_ERROR or "import mcp 失败")
                + "）"
            )
            for status in self._statuses:
                if status.enabled:
                    status.error = error
            logger.warning(error)
            return []

        enabled_servers = self.config.enabled_servers
        if not enabled_servers:
            return []

        # 在连接之前执行可选的本地启动脚本，把 streamable_http Server
        # 拉起来；脚本约定为幂等，已在运行时会快速返回。
        self._run_auto_start()

        self._ready_events = {
            server.name: threading.Event() for server in enabled_servers
        }
        self._thread = threading.Thread(
            target=self._thread_main,
            name="xagent-mcp-client",
            daemon=True,
        )
        self._thread.start()

        for server in enabled_servers:
            event = self._ready_events[server.name]
            wait_seconds = (
                max(server.connect_timeout, 5.0) + self.startup_wait_extra
            )
            if not event.wait(timeout=wait_seconds):
                status = self._status_by_name[server.name]
                status.error = f"连接超时（超过 {wait_seconds:g} 秒）"

        self._build_tools()
        atexit.register(self.close)
        return list(self._specs)

    def close(self) -> None:
        """关闭所有 MCP 会话。重复调用安全。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop

        if loop is not None and loop.is_running():
            for queue in list(self._queues.values()):
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:  # pragma: no cover - loop 已停止
                    pass

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    # -- 工具调用 ---------------------------------------------------------

    def call_tool(self, route: McpToolRoute, arguments: Mapping[str, Any] | None) -> str:
        """由 ``ToolSpec`` handler 调用：同步等待 MCP ``call_tool()``。"""
        payload = require_arguments(arguments)
        status = self._status_by_name.get(route.server)
        if status is None or not status.connected:
            detail = status.error if status is not None else "未找到该 Server 的配置"
            raise ToolError(
                f"MCP Server {route.server} 当前不可用"
                + (f"：{detail}" if detail else "")
            )

        queue = self._queues.get(route.server)
        loop = self._loop
        if queue is None or loop is None or not loop.is_running():
            raise ToolError(
                f"MCP Server {route.server} 的客户端未运行，无法调用工具 {route.tool}"
            )

        future: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def enqueue() -> None:
            try:
                queue.put_nowait((future, route.tool, payload))
            except Exception as exc:  # pragma: no cover
                if not future.done():
                    future.set_exception(exc)

        try:
            loop.call_soon_threadsafe(enqueue)
        except RuntimeError as exc:  # pragma: no cover
            raise ToolError(f"MCP 客户端事件循环已停止：{exc}") from exc

        server_config = self._config_by_name.get(route.server)
        wait_seconds = (server_config.timeout if server_config else 60.0) + 10.0
        try:
            result = future.result(timeout=wait_seconds)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ToolError(
                f"MCP 工具 {route.server}/{route.tool} 调用超时"
                f"（超过 {wait_seconds:g} 秒）"
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"MCP 工具 {route.server}/{route.tool} 调用失败："
                f"{_describe_exception(exc)}"
            ) from exc

        try:
            text, is_error = format_call_result(result)
            text = clip_result(text, self.max_result_chars)
        except Exception as exc:
            raise ToolError(
                f"MCP 工具 {route.server}/{route.tool} 的结果解析失败："
                f"{_describe_exception(exc)}"
            ) from exc
        if is_error:
            raise ToolError(
                f"MCP 工具 {route.server}/{route.tool} 返回错误：{text}"
            )
        return text

    # -- 内部实现 ---------------------------------------------------------

    def _run_auto_start(self) -> None:
        """执行 ``.mcp.json`` 中的 ``autoStart`` 命令（可选）。

        主要用途：本地 ``streamable_http`` Server 需要独立进程，但用户不想
        每次手动运行启动脚本。命令失败只记录摘要，不阻止后续连接尝试。
        """
        auto = self.config.auto_start
        if auto is None:
            return

        log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        out_path = log_dir / "auto-start.out.log"
        err_path = log_dir / "auto-start.err.log"

        env = dict(os.environ)
        env.update({key: str(value) for key, value in auto.env.items()})
        env.setdefault("XAGENT_PROJECT_DIR", str(Path(__file__).resolve().parent.parent.parent))

        # 注意：这里不能用 capture_output=True。启动脚本会拉起常驻子进程，
        # 子进程若继承了管道句柄，subprocess.run 会一直等管道 EOF 而卡住。
        # 直接重定向到日志文件，既不占用管道，也能保留启动输出。
        kwargs: dict[str, Any] = {
            "cwd": auto.cwd or None,
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": auto.timeout,
        }
        log_handles: list[Any] = []
        err_handle = None
        try:
            out_handle = out_path.open("w", encoding="utf-8", errors="replace")
            err_handle = err_path.open("w", encoding="utf-8", errors="replace")
            log_handles = [out_handle, err_handle]
            kwargs["stdout"] = out_handle
            kwargs["stderr"] = err_handle
        except OSError:
            pass
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [auto.command, *auto.args],
                **kwargs,
            )
        except FileNotFoundError:
            detail = f"找不到 autoStart 命令：{auto.command}"
            self._auto_start_message = f"MCP 自动启动失败：{detail}"
            self._write_auto_start_error(err_handle, detail)
            logger.warning(self._auto_start_message)
            return
        except subprocess.TimeoutExpired:
            detail = f"autoStart 执行超时（超过 {auto.timeout:g} 秒）"
            self._auto_start_message = f"MCP 自动启动失败：{detail}"
            self._write_auto_start_error(err_handle, detail)
            logger.warning(self._auto_start_message)
            return
        except Exception as exc:  # pragma: no cover - 环境相关错误
            detail = str(exc) or type(exc).__name__
            self._auto_start_message = f"MCP 自动启动失败：{detail}"
            self._write_auto_start_error(err_handle, detail)
            logger.warning(self._auto_start_message)
            return
        finally:
            for handle in log_handles:
                try:
                    handle.close()
                except OSError:  # pragma: no cover
                    pass

        elapsed = time.perf_counter() - started
        if completed.returncode == 0:
            self._auto_start_message = (
                f"MCP 自动启动：已执行（{elapsed:.1f}s，退出码 0）"
            )
        else:
            self._auto_start_message = (
                f"MCP 自动启动失败：退出码 {completed.returncode}"
                f"（日志：{out_path}）"
            )
            logger.warning(self._auto_start_message)

    @staticmethod
    def _write_auto_start_error(handle: Any, detail: str) -> None:
        if handle is None:
            return
        try:
            handle.write(detail + "\n")
            handle.flush()
        except OSError:  # pragma: no cover
            pass

    def _thread_main(self) -> None:  # pragma: no cover - 子线程
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:
            logger.exception("MCP 客户端事件循环异常退出")
        finally:
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:  # pragma: no cover
                pass
            finally:
                loop.close()

    async def _main(self) -> None:  # pragma: no cover - 子线程
        tasks: list[asyncio.Task[Any]] = []
        for server in self.config.enabled_servers:
            status = self._status_by_name[server.name]
            queue: asyncio.Queue[Any] = asyncio.Queue()
            self._queues[server.name] = queue
            event = self._ready_events[server.name]
            tasks.append(
                asyncio.create_task(
                    self._server_worker(server, status, queue, event)
                )
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _server_worker(  # pragma: no cover - 子线程
        self,
        server: McpServerConfig,
        status: McpServerStatus,
        queue: asyncio.Queue[Any],
        ready: threading.Event,
    ) -> None:
        stack = AsyncExitStack()
        entered = False
        try:
            await stack.__aenter__()
            entered = True
            # 注意：这里不能再额外包一层 anyio.fail_after()。MCP transport
            # 内部会创建 AnyIO cancel scope，而 cancel scope 必须与进入它的
            # 任务在同一生命周期内退出；超时由 HTTP client / ClientSession 的
            # 读超时控制。
            session = await self._open_session(stack, server)
            sdk_tools = await self._list_all_tools(session)

            discovered = [
                tool
                for tool in (
                    self._to_discovered(server.name, item) for item in sdk_tools
                )
                if tool is not None
            ]
            self._discovered.extend(discovered)
            status.connected = True
            status.tool_count = len(discovered)
            status.tools = tuple(tool.name for tool in discovered)
            ready.set()

            await self._serve(server, session, queue)
        except asyncio.CancelledError as exc:
            # 某些 transport 在连接失败时会先以 CancelledError 结束当前任务，
            # 真正的网络错误会在退出栈关闭时以 ExceptionGroup 抛出。
            logger.debug("MCP Server %s 连接任务被取消", server.name, exc_info=exc)
            context = exc.__cause__ or exc.__context__
            status.error = (
                _describe_exception(context) if context is not None else "连接已取消"
            )
            raise
        except TimeoutError:
            startup_timeout = min(server.timeout, max(server.connect_timeout, 5.0))
            status.error = f"连接或初始化超时（超过 {startup_timeout:g} 秒）"
        except Exception as exc:
            status.error = _describe_exception(exc)
        finally:
            ready.set()
            if entered:
                try:
                    await stack.aclose()
                except BaseException as exc:
                    # 连接失败时真正的错误（如 DNS / ConnectError）常常在
                    # transport 的 TaskGroup 退出时才浮现，不能被静默丢弃。
                    logger.debug(
                        "MCP Server %s 关闭连接时出错", server.name, exc_info=exc
                    )
                    if not status.connected:
                        detail = _describe_exception(exc)
                        if detail and "超时" not in status.error:
                            status.error = detail

    async def _open_session(  # pragma: no cover - 子线程
        self,
        stack: AsyncExitStack,
        server: McpServerConfig,
    ) -> Any:
        if server.transport == "stdio":
            env = None
            if server.env:
                base = (
                    dict(get_default_environment())
                    if callable(get_default_environment)
                    else dict(os.environ)
                )
                base.update(server.env)
                env = base
            parameters = StdioServerParameters(
                command=server.command,
                args=list(server.args),
                env=env,
                cwd=server.cwd or None,
            )
            streams = await stack.enter_async_context(stdio_client(parameters))
        else:
            streams = await self._open_network_transport(stack, server)

        read_stream, write_stream = streams[0], streams[1]
        # 连接/发现阶段使用 connect_timeout 兜底；真正的 call_tool() 会在
        # 每次调用时显式传入 server.timeout，因此不会受这里的初始化超时影响。
        startup_timeout = min(server.timeout, max(server.connect_timeout, 5.0))
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=_timeout_arg(startup_timeout),
            )
        )
        await session.initialize()
        return session

    async def _open_network_transport(  # pragma: no cover - 子线程
        self,
        stack: AsyncExitStack,
        server: McpServerConfig,
    ) -> Any:
        headers = dict(server.headers) if server.headers else None

        if server.transport == "sse":
            if _sse_client_factory is None:
                raise RuntimeError("当前 MCP SDK 不支持 SSE 传输")
            manager = _call_with_supported_kwargs(
                _sse_client_factory,
                server.url,
                headers=headers,
                timeout=min(server.connect_timeout, 30.0),
                sse_read_timeout=server.timeout,
            )
            return await stack.enter_async_context(manager)

        if _http_client_factory is None:
            raise RuntimeError("当前 MCP SDK 不支持 Streamable HTTP 传输")

        if _HTTP_TRANSPORT_IS_V2:
            # 2.x 通过自定义 httpx client 传递 headers / 超时；该 client 由
            # 当前 Server 的退出栈负责关闭。
            from mcp.shared._httpx_utils import create_mcp_http_client

            http_client = create_mcp_http_client(
                headers=headers,
                timeout=_http_timeout(server.connect_timeout, server.timeout),
            )
            await stack.enter_async_context(http_client)
            manager = _call_with_supported_kwargs(
                _http_client_factory,
                server.url,
                http_client=http_client,
            )
            return await stack.enter_async_context(manager)

        # 1.x：streamablehttp_client 直接接受 headers / timeout。
        manager = _call_with_supported_kwargs(
            _http_client_factory,
            server.url,
            headers=headers,
            timeout=min(server.connect_timeout, 30.0),
            sse_read_timeout=server.timeout,
        )
        return await stack.enter_async_context(manager)

    async def _list_all_tools(self, session: Any) -> list[Any]:  # pragma: no cover
        tools: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(50):
            if cursor:
                result = await session.list_tools(cursor=cursor)
            else:
                result = await session.list_tools()
            page = _get_field(result, "tools", default=[]) or []
            tools.extend(list(page))
            cursor = _get_field(result, "next_cursor", "nextCursor", default=None)
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        return tools

    def _to_discovered(
        self, server_name: str, tool: Any
    ) -> DiscoveredTool | None:  # pragma: no cover
        name = str(_get_field(tool, "name", default="") or "").strip()
        if not name:
            return None
        parameters = _get_field(
            tool,
            "input_schema",
            "inputSchema",
            "parameters",
            default=None,
        )
        return DiscoveredTool(
            server=server_name,
            name=name,
            description=str(_get_field(tool, "description", default="") or ""),
            parameters=parameters if isinstance(parameters, Mapping) else None,
        )

    async def _serve(  # pragma: no cover - 子线程
        self,
        server: McpServerConfig,
        session: Any,
        queue: asyncio.Queue[Any],
    ) -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            future, tool_name, arguments = item
            try:
                result = await session.call_tool(
                    tool_name,
                    arguments=arguments,
                    read_timeout_seconds=_timeout_arg(server.timeout),
                )
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)

    def _build_tools(self) -> None:
        result = build_mcp_tools(
            self._discovered,
            self._reserved_names,
            self.call_tool,
        )
        self._specs = result.specs
        self._routes = dict(result.routes)


__all__ = ["McpManager", "McpServerStatus"]
