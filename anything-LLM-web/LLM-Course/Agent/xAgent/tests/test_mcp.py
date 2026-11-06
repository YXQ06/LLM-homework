"""MCP Client 离线单元测试：配置解析、工具映射与结果格式化。

这些测试不会连接真实 MCP Server，也不依赖本机是否安装 mcp SDK。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xAgent.core.mcp import (
    DiscoveredTool,
    McpAutoStart,
    McpConfig,
    McpManager,
    McpServerConfig,
    build_mcp_tools,
    create_mcp_manager,
    find_mcp_config_path,
    format_call_result,
    load_mcp_config,
)
from xAgent.core.mcp.tools import McpToolRoute, sanitize_tool_name


class McpConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, data: object) -> Path:
        path = self.root / ".mcp.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def test_parse_stdio_and_streamable_http(self) -> None:
        self._write(
            {
                "mcpServers": {
                    "local": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["server.py", "--flag"],
                        "env": {"TOKEN": "abc"},
                        "cwd": ".",
                    },
                    "remote": {
                        "type": "streamable-http",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer x"},
                        "timeout": 30,
                    },
                    "disabled": {
                        "type": "http",
                        "url": "https://example.com/other",
                        "enabled": False,
                    },
                }
            }
        )

        config = load_mcp_config(project_root=self.root)

        self.assertEqual(config.issues, ())
        self.assertEqual(len(config.servers), 3)
        local, remote, disabled = config.servers
        self.assertEqual(local.transport, "stdio")
        self.assertEqual(local.args, ("server.py", "--flag"))
        self.assertEqual(local.env["TOKEN"], "abc")
        self.assertEqual(remote.transport, "streamable_http")
        self.assertEqual(remote.headers["Authorization"], "Bearer x")
        self.assertEqual(remote.timeout, 30.0)
        self.assertFalse(disabled.enabled)
        self.assertEqual(
            [server.name for server in config.enabled_servers],
            ["local", "remote"],
        )

    def test_invalid_json_is_reported_without_raising(self) -> None:
        (self.root / ".mcp.json").write_text("{not json", encoding="utf-8")
        config = load_mcp_config(project_root=self.root)
        self.assertEqual(config.servers, ())
        self.assertTrue(config.issues)
        self.assertIn("JSON", config.issues[0])

    def test_missing_command_or_url_is_reported(self) -> None:
        self._write(
            {
                "mcpServers": {
                    "bad-stdio": {"type": "stdio"},
                    "bad-http": {"type": "streamable_http"},
                }
            }
        )
        config = load_mcp_config(project_root=self.root)
        self.assertEqual(config.servers, ())
        self.assertEqual(len(config.issues), 2)

    def test_find_config_walks_up_from_child_directory(self) -> None:
        self._write({"mcpServers": {"x": {"command": "python"}}})
        child = self.root / "a" / "b"
        child.mkdir(parents=True)
        self.assertEqual(
            find_mcp_config_path(child),
            self.root / ".mcp.json",
        )

    def test_parse_auto_start_and_resolve_cwd(self) -> None:
        self._write(
            {
                "mcpServers": {"remote": {"url": "https://example.com/mcp"}},
                "autoStart": {
                    "command": "powershell",
                    "args": ["-NoProfile", "-File", "start.ps1"],
                    "cwd": "scripts",
                    "env": {"TOKEN": "abc"},
                    "timeout": 12,
                },
            }
        )
        config = load_mcp_config(project_root=self.root)
        auto = config.auto_start
        self.assertIsNotNone(auto)
        self.assertEqual(auto.command, "powershell")
        self.assertEqual(auto.args, ("-NoProfile", "-File", "start.ps1"))
        self.assertEqual(auto.cwd, str((self.root / "scripts").resolve()))
        self.assertEqual(auto.env["TOKEN"], "abc")
        self.assertEqual(auto.timeout, 12.0)

    def test_auto_start_accepts_command_string(self) -> None:
        self._write(
            {
                "mcpServers": {"remote": {"url": "https://example.com/mcp"}},
                "autoStart": "python start_servers.py --wait",
            }
        )
        auto = load_mcp_config(project_root=self.root).auto_start
        self.assertIsNotNone(auto)
        self.assertEqual(auto.command, "python")
        self.assertEqual(auto.args, ("start_servers.py", "--wait"))
        self.assertEqual(auto.cwd, str(self.root.resolve()))

    def test_env_var_can_point_to_custom_file(self) -> None:
        custom = self.root / "custom-mcp.json"
        custom.write_text(
            json.dumps({"mcpServers": {"custom": {"url": "https://a/mcp"}}}),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"XAGENT_MCP_CONFIG": str(custom)}):
            config = load_mcp_config()
        self.assertEqual(config.path, custom)
        self.assertEqual(config.servers[0].name, "custom")
        self.assertEqual(config.servers[0].transport, "streamable_http")


class McpToolMappingTest(unittest.TestCase):
    def test_unique_tool_keeps_original_name(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def caller(route: McpToolRoute, arguments: dict) -> str:
            calls.append((route.server, route.tool, dict(arguments)))
            return "ok"

        result = build_mcp_tools(
            [
                DiscoveredTool(
                    server="Bazi-MCP",
                    name="get_fortune",
                    description="查询运势",
                    parameters={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                )
            ],
            reserved_names=["fetchWeb", "RunCli"],
            caller=caller,
        )

        self.assertEqual([spec.name for spec in result.specs], ["get_fortune"])
        spec = result.specs[0]
        self.assertIn("Bazi-MCP", spec.description)
        self.assertEqual(spec.required_parameters, ("name",))
        self.assertEqual(spec.handler({"name": "张三"}), "ok")
        self.assertEqual(calls, [("Bazi-MCP", "get_fortune", {"name": "张三"})])

    def test_duplicate_names_between_servers_get_server_prefix(self) -> None:
        result = build_mcp_tools(
            [
                DiscoveredTool(server="server-a", name="search"),
                DiscoveredTool(server="server-b", name="search"),
            ],
            reserved_names=[],
            caller=lambda route, arguments: "ok",
        )

        names = [spec.name for spec in result.specs]
        self.assertEqual(names, ["server-a__search", "server-b__search"])
        self.assertEqual(result.routes["server-a__search"].server, "server-a")
        self.assertEqual(result.routes["server-b__search"].tool, "search")

    def test_conflict_with_local_tool_gets_server_prefix(self) -> None:
        result = build_mcp_tools(
            [DiscoveredTool(server="my-server", name="fetchWeb")],
            reserved_names=["fetchWeb"],
            caller=lambda route, arguments: "ok",
        )
        self.assertEqual(result.specs[0].name, "my-server__fetchWeb")

    def test_invalid_tool_name_is_sanitized(self) -> None:
        self.assertEqual(sanitize_tool_name("天气.查询"), "tool")
        self.assertEqual(sanitize_tool_name("my.tool/v2"), "my_tool_v2")

        result = build_mcp_tools(
            [DiscoveredTool(server="a", name="my.tool/v2")],
            reserved_names=[],
            caller=lambda route, arguments: "ok",
        )
        self.assertEqual(result.specs[0].name, "my_tool_v2")

    def test_bad_schema_falls_back_to_empty_object(self) -> None:
        result = build_mcp_tools(
            [DiscoveredTool(server="a", name="tool", parameters={"type": "string"})],
            reserved_names=[],
            caller=lambda route, arguments: "ok",
        )
        self.assertEqual(result.specs[0].parameters["type"], "object")
        self.assertEqual(result.specs[0].parameters["properties"], {})


class McpResultFormattingTest(unittest.TestCase):
    def test_text_and_structured_content(self) -> None:
        result = {
            "content": [{"type": "text", "text": "第一段"}],
            "structured_content": {"score": 99, "name": "测试"},
            "is_error": False,
        }
        text, is_error = format_call_result(result)
        self.assertFalse(is_error)
        self.assertIn("第一段", text)
        self.assertIn('"score": 99', text)

    def test_error_flag_is_returned(self) -> None:
        result = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="参数错误")],
            structured_content=None,
            is_error=True,
        )
        text, is_error = format_call_result(result)
        self.assertTrue(is_error)
        self.assertIn("参数错误", text)

    def test_camel_case_fields_are_supported(self) -> None:
        result = {
            "content": [{"type": "text", "text": "ok"}],
            "structuredContent": {"value": 1},
            "isError": False,
        }
        text, is_error = format_call_result(result)
        self.assertFalse(is_error)
        self.assertIn("ok", text)
        self.assertIn('"value": 1', text)

    def test_image_and_resource_blocks_are_described(self) -> None:
        result = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="image", data="a" * 16, mime_type="image/png"
                ),
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///a.txt",
                        "mime_type": "text/plain",
                        "text": "资源正文",
                    },
                },
            ],
            is_error=False,
        )
        text, _ = format_call_result(result)
        self.assertIn("图片内容", text)
        self.assertIn("image/png", text)
        self.assertIn("file:///a.txt", text)
        self.assertIn("资源正文", text)


class McpAutoStartTest(unittest.TestCase):
    def _config(self) -> McpConfig:
        return McpConfig(
            servers=(
                McpServerConfig(
                    name="remote",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                ),
            ),
            auto_start=McpAutoStart(
                command="python",
                args=("start.py",),
                cwd=".",
                timeout=10,
            ),
        )

    def test_auto_start_success_is_reported(self) -> None:
        manager = McpManager(self._config())
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with mock.patch(
            "xAgent.core.mcp.client.subprocess.run", return_value=completed
        ) as run:
            manager._run_auto_start()

        run.assert_called_once()
        self.assertIn("已执行", manager.startup_messages()[0])

    def test_auto_start_failure_is_reported_without_raising(self) -> None:
        manager = McpManager(self._config())
        completed = SimpleNamespace(returncode=2, stdout="", stderr="bad")
        with mock.patch(
            "xAgent.core.mcp.client.subprocess.run", return_value=completed
        ):
            manager._run_auto_start()

        self.assertIn("退出码 2", manager.startup_messages()[0])

    def test_auto_start_missing_command_is_reported(self) -> None:
        manager = McpManager(self._config())
        with mock.patch(
            "xAgent.core.mcp.client.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            manager._run_auto_start()

        self.assertIn("找不到", manager.startup_messages()[0])


class McpManagerWithoutSdkTest(unittest.TestCase):
    def test_empty_config_returns_no_tools(self) -> None:
        manager = McpManager(McpConfig())
        self.assertEqual(manager.start(), [])
        self.assertEqual(manager.statuses, ())

    def test_missing_sdk_reports_error_without_raising(self) -> None:
        config = McpConfig(
            servers=(
                McpServerConfig(
                    name="remote",
                    transport="streamable_http",
                    url="https://example.com/mcp",
                ),
            )
        )
        with mock.patch(
            "xAgent.core.mcp.client._MCP_AVAILABLE", False
        ), mock.patch(
            "xAgent.core.mcp.client._MCP_IMPORT_ERROR", "No module named 'mcp'"
        ):
            manager = McpManager(config)
            self.assertEqual(manager.start(), [])

        status = manager.statuses[0]
        self.assertFalse(status.connected)
        self.assertIn("mcp", status.error.lower())
        messages = manager.startup_messages()
        self.assertTrue(any("连接失败" in message for message in messages))

    def test_create_manager_uses_agent_result_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".mcp.json"
            path.write_text(
                json.dumps({"mcpServers": {}}), encoding="utf-8"
            )
            manager = create_mcp_manager(
                SimpleNamespace(max_tool_result_chars=3456),
                project_root=tmp,
            )
            self.assertEqual(manager.max_result_chars, 3456)


if __name__ == "__main__":
    unittest.main()
