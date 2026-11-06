"""真实 MCP Server 联网冒烟测试（默认跳过）。

在项目根目录准备好 ``.mcp.json`` 后执行：

    $env:XAGENT_LIVE_MCP = "1"
    python -m unittest xAgent.tests.test_mcp_live -v

测试会连接配置中的所有 MCP Server，列出工具；若存在
``getChineseCalendar``（Bazi 或 Bazi-MCP），还会实际调用一次，验证
Function Calling 的转发与结果格式化链路。
"""

from __future__ import annotations

import os
import unittest

from xAgent.core.config import AgentConfig, SearchConfig
from xAgent.core.mcp import create_mcp_manager, register_mcp_tools
from xAgent.core.tools import build_default_registry


@unittest.skipUnless(
    os.environ.get("XAGENT_LIVE_MCP") == "1",
    "设置 XAGENT_LIVE_MCP=1 后才执行真实 MCP 联网测试",
)
class LiveMcpTest(unittest.TestCase):
    def test_connect_list_and_call(self) -> None:
        registry = build_default_registry(AgentConfig(), SearchConfig())
        manager = create_mcp_manager(AgentConfig())
        try:
            registered = register_mcp_tools(registry, manager)
            print("MCP 配置：", manager.config.path)
            for message in manager.startup_messages():
                print(message)

            connected = [status for status in manager.statuses if status.connected]
            self.assertTrue(
                connected,
                "没有任何 MCP Server 连接成功："
                + "；".join(manager.startup_messages()),
            )
            self.assertTrue(registered, "MCP Server 已连接但没有发现工具")

            for status in connected:
                self.assertGreater(status.tool_count, 0)
                # 重名工具会自动加 <server>__<tool> 前缀，因此要用管理器
                # 记录的“对外暴露名称”来检查注册结果，而不是原始工具名。
                exposed_names = manager.server_tools.get(status.name, ())
                self.assertEqual(len(exposed_names), status.tool_count)
                for tool_name in exposed_names:
                    self.assertIn(tool_name, registry.names)

            calendar_tool = next(
                (
                    name
                    for server_name in ("Bazi", "Bazi-MCP")
                    for name in manager.server_tools.get(server_name, ())
                    if name.endswith("getChineseCalendar")
                ),
                None,
            )
            if calendar_tool:
                result = registry.execute(calendar_tool, {})
                self.assertTrue(result.ok, result.content)
                self.assertTrue(result.content.strip())
                print(f"{calendar_tool} 返回：", result.content[:300])
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
