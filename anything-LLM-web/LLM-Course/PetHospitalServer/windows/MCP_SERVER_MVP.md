# 宠物医院 MCP Server - MVP 版本

## 目标

开发一个最小可用的 MCP Server，只实现 `list_pets` 功能，支持多种筛选参数。

## 技术栈

- **Python 3.10+**
- **MCP SDK v2** (`mcp[cli]`)
- **Streamable HTTP** 传输

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装 MCP SDK
pip install "mcp[cli]>=2.0.0"

# 安装其他依赖
pip install httpx
```

### 2. 项目结构

```
pet-hospital-mcp-mvp/
├── server.py              # MCP Server 主程序
├── config.py              # 配置管理
├── requirements.txt       # 依赖列表
└── README.md
```

### 3. 核心代码

#### server.py

```python
"""
宠物医院 MCP Server - MVP 版本
只实现 list_pets 功能
"""

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

# 创建 MCP Server
mcp = MCPServer("pet-hospital-mcp")

# REST API 配置
REST_API_BASE = "http://127.0.0.1:8080"


async def fetch_pets(params: dict) -> dict:
    """从宠物医院 REST API 获取宠物列表"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{REST_API_BASE}/api/v1/pets",
                params=params,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            return {"code": 500, "message": f"API 请求失败: {str(e)}", "data": None}


@mcp.tool(
    name="list_pets",
    description="获取宠物医院档案列表，支持按物种、医生、状态、关键词等条件筛选",
    annotations=ToolAnnotations(
        title="宠物列表查询",
        readOnlyHint=True,  # 只读操作
        openWorldHint=False  # 不涉及外部世界
    )
)
async def list_pets(
    q: str = "",
    species: str = "",
    doctor: str = "",
    status: str = "",
    min_cost: float = 0,
    max_cost: float = 0,
    sort_by: str = "",
    order: str = "asc",
    page: int = 1,
    page_size: int = 20
) -> str:
    """
    获取宠物医院档案列表

    Args:
        q: 搜索关键词（跨字段搜索，空格分词为AND逻辑）
        species: 物种筛选 (犬/猫/兔/仓鼠/鹦鹉/龟/马)
        doctor: 主治医生姓名
        status: 就诊状态 (待就诊/就诊中/住院中/已康复/慢性病随访)
        min_cost: 最低总花费金额
        max_cost: 最高总花费金额
        sort_by: 排序字段 (name/totalCost/visitCount/createdAt)
        order: 排序方向 (asc/desc)
        page: 页码（默认1）
        page_size: 每页数量（默认20）

    Returns:
        JSON 格式的宠物列表数据
    """
    # 构建查询参数
    params = {}

    if q:
        params["q"] = q
    if species:
        params["species"] = species
    if doctor:
        params["doctor"] = doctor
    if status:
        params["status"] = status
    if min_cost > 0:
        params["min"] = min_cost
    if max_cost > 0:
        params["max"] = max_cost
    if sort_by:
        params["sortBy"] = sort_by
    if order:
        params["order"] = order
    if page > 1:
        params["page"] = page
    if page_size != 20:
        params["pageSize"] = page_size

    # 调用 REST API
    result = await fetch_pets(params)

    # 返回格式化的结果
    if result.get("code") == 200 and result.get("data"):
        return format_pets_response(result["data"])
    else:
        return f"查询失败: {result.get('message', '未知错误')}"


def format_pets_response(data: dict) -> str:
    """格式化宠物列表响应"""
    pets = data.get("pets", [])
    total = data.get("total", 0)
    page = data.get("page", 1)
    page_size = data.get("pageSize", 20)

    if not pets:
        return "未找到符合条件的宠物档案"

    lines = [f"=== 宠物列表 (共 {total} 条，第 {page} 页) ===\n"]

    for pet in pets:
        pet_id = pet.get("id", "N/A")
        name = pet.get("name", "N/A")
        species = pet.get("species", "N/A")
        breed = pet.get("breed", "N/A")
        owner = pet.get("ownerName", "N/A")
        doctor = pet.get("doctor", "N/A")
        disease = pet.get("disease", "N/A")
        status = pet.get("status", "N/A")
        total_cost = pet.get("totalCost", 0)
        visit_count = pet.get("visitCount", 0)

        lines.append(f"【{name}】 {pet_id}")
        lines.append(f"  物种: {species} | 品种: {breed}")
        lines.append(f"  主人: {owner}")
        lines.append(f"  医生: {doctor} | 疾病: {disease}")
        lines.append(f"  状态: {status}")
        lines.append(f"  总花费: ¥{total_cost:.2f} | 就诊次数: {visit_count}")
        lines.append("")

    # 分页信息
    total_pages = (total + page_size - 1) // page_size
    lines.append(f"--- 第 {page}/{total_pages} 页 ---")

    return "\n".join(lines)


# 运行服务器
if __name__ == "__main__":
    import asyncio

    print("🐾 宠物医院 MCP Server 启动中...")
    print(f"📡 代理 REST API: {REST_API_BASE}")
    print("🔌 传输方式: Streamable HTTP")
    print("📍 监听地址: http://localhost:8000/mcp")
    print("\n按 Ctrl+C 停止服务\n")

    mcp.run(transport="streamable-http")
```

#### config.py

```python
"""配置管理"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """服务器配置"""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    rest_api_url: str = "http://127.0.0.1:8080"
    log_level: str = "info"


def load_config() -> Config:
    """从环境变量加载配置"""
    return Config(
        mcp_host=os.getenv("MCP_HOST", "0.0.0.0"),
        mcp_port=int(os.getenv("MCP_PORT", "8000")),
        rest_api_url=os.getenv("REST_API_URL", "http://127.0.0.1:8080"),
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
```

#### requirements.txt

```
mcp[cli]>=2.0.0
httpx>=0.27.0
```

## 运行方式

### 1. 启动宠物医院 REST API

```bash
# 确保宠物医院服务已启动
./pethospital.exe  # 或 pethospital
```

### 2. 启动 MCP Server

```bash
# 方式1: 直接运行
python server.py

# 方式2: 使用 mcp dev（开发模式，带调试界面）
mcp dev server.py
```

### 3. 测试 MCP Server

#### 使用 MCP Inspector

```bash
# 安装 MCP Inspector
npx -y @modelcontextprotocol/inspector

# 连接到 http://localhost:8000/mcp
```

#### 使用 Python Client 测试

```python
# test_client.py
import asyncio
from mcp import ClientSession
from mcp.transport.streamable_http import streamablehttp_client

async def main():
    # 连接到 MCP Server
    async with streamablehttp_client("http://localhost:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            # 初始化
            await session.initialize()

            # 列出可用工具
            tools = await session.list_tools()
            print("可用工具:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")

            # 调用 list_pets 工具
            print("\n查询所有宠物:")
            result = await session.call_tool("list_pets", arguments={})
            print(result.content[0].text)

            # 带参数查询
            print("\n查询所有犬类宠物:")
            result = await session.call_tool("list_pets", arguments={"species": "犬"})
            print(result.content[0].text)

if __name__ == "__main__":
    asyncio.run(main())
```

## 功能特性

### 支持的筛选参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `q` | string | 关键词搜索（跨字段） | `q=感冒` |
| `species` | string | 物种筛选 | `species=犬` |
| `doctor` | string | 医生筛选 | `doctor=李医生` |
| `status` | string | 状态筛选 | `status=就诊中` |
| `min_cost` | float | 最低花费 | `min_cost=1000` |
| `max_cost` | float | 最高花费 | `max_cost=5000` |
| `sort_by` | string | 排序字段 | `sort_by=totalCost` |
| `order` | string | 排序方向 | `order=desc` |
| `page` | int | 页码 | `page=2` |
| `page_size` | int | 每页数量 | `page_size=10` |

### 使用示例

```python
# 基础查询
result = await session.call_tool("list_pets")

# 按物种筛选
result = await session.call_tool("list_pets", arguments={"species": "犬"})

# 按医生和状态筛选
result = await session.call_tool("list_pets", arguments={
    "doctor": "李医生",
    "status": "就诊中"
})

# 按花费区间筛选并排序
result = await session.call_tool("list_pets", arguments={
    "min_cost": 1000,
    "max_cost": 10000,
    "sort_by": "totalCost",
    "order": "desc"
})

# 分页查询
result = await session.call_tool("list_pets", arguments={
    "page": 2,
    "page_size": 10
})
```

## 响应格式示例

```
=== 宠物列表 (共 156 条，第 1 页) ===

【旺财】 PET-000001
  物种: 犬 | 品种: 金毛
  主人: 张三
  医生: 李医生 | 疾病: 急性肠胃炎
  状态: 就诊中
  总花费: ¥1,280.00 | 就诊次数: 3

【小花】 PET-000002
  物种: 猫 | 品种: 英短
  主人: 王五
  医生: 赵医生 | 疾病: 皮肤真菌感染
  状态: 已康复
  总花费: ¥680.00 | 就诊次数: 2

--- 第 1/8 页 ---
```

## 下一步扩展

完成 MVP 验证后，可以按以下顺序添加功能：

1. **get_pet** - 获取单个宠物详情
2. **create_pet** - 创建宠物档案
3. **update_pet** - 更新宠物档案
4. **delete_pet** - 删除宠物档案
5. **get_pet_records** - 获取病历
6. **add_pet_record** - 添加病历
7. **get_pet_charges** - 获取消费明细
8. **add_pet_charge** - 添加收费
9. **get_stats** - 获取统计信息

## 故障排查

### 1. 连接失败

```
错误: Connection refused
```

**解决**: 确保宠物医院 REST API 已启动在 `http://127.0.0.1:8080`

### 2. 依赖缺失

```
ModuleNotFoundError: No module named 'mcp'
```

**解决**: 重新安装依赖
```bash
pip install "mcp[cli]>=2.0.0"
```

### 3. 端口被占用

```
OSError: [Errno 98] Address already in use
```

**解决**: 更换端口
```bash
export MCP_PORT=8001
python server.py
```

## 参考资源

- [MCP Python SDK v2 文档](https://py.sdk.modelcontextprotocol.io/)
- [MCP 2026-07-28 规范](https://modelcontextprotocol.io/specification/2026-07-28)
- [宠物医院 REST API 文档](./README.md)
