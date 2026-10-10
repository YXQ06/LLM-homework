"""
MCP Server 测试客户端
用于测试所有 MCP 工具
"""

import asyncio
from mcp import ClientSession
from mcp.transport.streamable_http import streamablehttp_client


async def main():
    """主测试函数"""
    server_url = "http://localhost:8000/mcp"

    print("🐾 连接到宠物医院 MCP Server...")
    print(f"📍 服务器地址: {server_url}\n")

    try:
        # 连接到 MCP Server
        async with streamablehttp_client(server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                # 初始化
                await session.initialize()
                print("✅ 连接成功!\n")

                # 列出可用工具
                print("=== 可用工具 ===")
                tools = await session.list_tools()
                for tool in tools.tools:
                    print(f"  - {tool.name}: {tool.description}")
                print()

                # 测试1: 基础查询（所有宠物）
                print("=== 测试1: 查询所有宠物 ===")
                result = await session.call_tool("list_pets", arguments={})
                print(result.content[0].text)
                print()

                # 测试2: 按物种筛选
                print("=== 测试2: 查询犬类宠物 ===")
                result = await session.call_tool("list_pets", arguments={"species": "犬"})
                print(result.content[0].text)
                print()

                # 测试3: 获取单个宠物详情
                print("=== 测试3: 获取宠物详情 (PET-000001) ===")
                result = await session.call_tool("get_pet", arguments={"pet_id": "PET-000001"})
                print(result.content[0].text)
                print()

                # 测试4: 创建新宠物
                print("=== 测试4: 创建新宠物 ===")
                result = await session.call_tool("create_pet", arguments={
                    "name": "小黄",
                    "species": "犬",
                    "breed": "柴犬",
                    "gender": "公",
                    "age_months": 24,
                    "owner_name": "王五",
                    "owner_phone": "13800005555",
                    "doctor": "李医生",
                    "disease": "感冒",
                    "status": "待就诊"
                })
                print(result.content[0].text)
                print()

                # 测试5: 更新宠物信息
                print("=== 测试5: 更新宠物信息 ===")
                result = await session.call_tool("update_pet", arguments={
                    "pet_id": "PET-000001",
                    "status": "已康复",
                    "notes": "已完全康复"
                })
                print(result.content[0].text)
                print()

                # 测试6: 获取病历记录
                print("=== 测试6: 获取病历记录 ===")
                result = await session.call_tool("get_pet_records", arguments={"pet_id": "PET-000001"})
                print(result.content[0].text)
                print()

                # 测试7: 添加病历记录
                print("=== 测试7: 添加病历记录 ===")
                result = await session.call_tool("add_pet_record", arguments={
                    "pet_id": "PET-000001",
                    "doctor": "李医生",
                    "diagnosis": "复查正常",
                    "symptoms": "无",
                    "treatment": "继续观察",
                    "charge": 50
                })
                print(result.content[0].text)
                print()

                # 测试8: 获取消费明细
                print("=== 测试8: 获取消费明细 ===")
                result = await session.call_tool("get_pet_charges", arguments={"pet_id": "PET-000001"})
                print(result.content[0].text)
                print()

                # 测试9: 添加收费记录
                print("=== 测试9: 添加收费记录 ===")
                result = await session.call_tool("add_pet_charge", arguments={
                    "pet_id": "PET-000001",
                    "item": "疫苗接种",
                    "category": "疫苗",
                    "amount": 120,
                    "doctor": "李医生"
                })
                print(result.content[0].text)
                print()

                # 测试10: 全文检索
                print("=== 测试10: 全文检索 '骨折' ===")
                result = await session.call_tool("search_pets", arguments={"query": "骨折"})
                print(result.content[0].text)
                print()

                # 测试11: 获取统计数据
                print("=== 测试11: 获取统计数据 ===")
                result = await session.call_tool("get_stats", arguments={})
                print(result.content[0].text)
                print()

                # 测试12: 导出数据
                print("=== 测试12: 导出数据 (JSON) ===")
                result = await session.call_tool("export_data", arguments={"format": "json"})
                print(result.content[0].text)
                print()

                # 测试13: 删除宠物（使用刚创建的宠物）
                print("=== 测试13: 删除宠物 ===")
                # 先查询刚创建的宠物
                result = await session.call_tool("list_pets", arguments={"q": "小黄"})
                print("查询要删除的宠物:")
                print(result.content[0].text)
                print()
                
                # 注意：这里不实际删除，只是演示
                print("（跳过删除测试，保留数据）")
                print()

                print("✅ 所有测试完成!")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        print("\n请确保:")
        print("1. 宠物医院 REST API 已启动在 http://127.0.0.1:8080")
        print("2. MCP Server 已启动在 http://localhost:8000")


if __name__ == "__main__":
    asyncio.run(main())
