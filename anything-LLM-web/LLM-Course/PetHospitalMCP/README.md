# 🐾 宠物医院 MCP Server

把宠物医院 REST API（`pethospital.exe`）封装成 **12 个 MCP 工具**，覆盖档案 CRUD、病历、收费、检索、统计与导出的完整业务闭环。供 xAgent 等支持 MCP 的 Agent 客户端调用。

## 功能特性

### 档案管理
- ✅ `list_pets` —— 获取宠物档案列表，支持按物种、医生、状态、关键词、花费区间筛选，支持排序与分页
- ✅ `get_pet` —— 获取单个宠物详情（含病历与消费记录）
- ✅ `create_pet` —— 创建宠物档案（物种 / 性别 / 状态做枚举校验）
- ✅ `update_pet` —— 更新宠物档案（只传需要修改的字段）
- ✅ `delete_pet` —— 删除宠物档案
- ✅ `search_pets` —— 全文检索（跨字段，含病历全文）

### 病历与收费
- ✅ `get_pet_records` —— 获取历史病历
- ✅ `add_pet_record` —— 添加病历（诊断、症状、治疗、处方、费用）
- ✅ `get_pet_charges` —— 获取消费明细
- ✅ `add_pet_charge` —— 添加收费记录

### 统计与导出
- ✅ `get_stats` —— 经营统计（总量、收入、物种分布、医生排行、疾病分布）
- ✅ `export_data` —— 导出数据（json / csv）

## 快速开始

### 前置条件

1. **Python 3.10+**
2. **宠物医院 REST API** (`pethospital.exe`)，默认监听 `http://127.0.0.1:8080`

### 安装和运行

#### 方式1: 使用启动脚本

```bash
# Windows
start.bat

# Linux / macOS
bash start.sh
```

#### 方式2: 手动运行

```bash
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux / macOS

# 2. 安装依赖
pip install -r requirements.txt

# 3. 确保宠物医院 API 已启动
./pethospital.exe

# 4. 启动 MCP Server
python server.py
```

#### 方式3: 使用 uv (推荐)

```bash
pip install uv
uv run --with "mcp[cli]>=2.0.0" --with httpx server.py
```

### 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOSPITAL_API_BASE` | `http://127.0.0.1:8080` | 上游 REST API 地址，换端口 / 换机器时设置，无需改源码 |
| `LOG_LEVEL` | `INFO` | 日志等级（DEBUG / INFO / WARNING） |

## 错误返回格式

与同仓库的 AnythingLLM MCP 保持同一契约：所有工具失败时返回结构化 JSON，
Agent 客户端可直接解析 `code` 做分支，无需对自然语言做模糊匹配。

```json
{"ok": false, "error": {"code": 400, "message": "创建失败：物种必须是 ... 中的一种"}}
```

常用错误码：`400` 参数校验失败（物种 / 性别 / 状态枚举、负数月龄等）；`502` 上游 REST API 不可达（已自动重试 2 次，退避 0.5s/1s，覆盖 429/500/502/503/504 与网络错误）。

## 测试

### 使用测试客户端

```bash
# 在另一个终端运行
python test_client.py
```

### 使用 MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
# 在浏览器中打开显示的 URL，连接到 http://localhost:8000/mcp
```

### 使用 curl 测试

```bash
# 列出工具
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 调用 list_pets
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬"}}}'
```

## list_pets 筛选参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `q` | string | 关键词搜索 | `q=感冒` |
| `species` | string | 物种 | `species=犬` |
| `doctor` | string | 医生 | `doctor=李医生` |
| `status` | string | 状态 | `status=就诊中` |
| `min_cost` | float | 最低花费 | `min_cost=1000` |
| `max_cost` | float | 最高花费 | `max_cost=5000` |
| `sort_by` | string | 排序字段 | `sort_by=totalCost` |
| `order` | string | 排序方向 | `order=desc` |
| `page` | int | 页码 | `page=2` |
| `page_size` | int | 每页数量 | `page_size=10` |

## 项目结构

```
PetHospitalMCP/
├── server.py          # MCP Server 主程序（12 个工具）
├── test_client.py     # 测试客户端
├── requirements.txt   # 依赖列表
├── start.bat          # Windows 启动脚本
├── start.sh           # Linux / macOS 启动脚本
└── README.md          # 说明文档
```

## 故障排查

### 1. 连接失败

```
错误: Connection refused
```

**解决**:
- 确保宠物医院 REST API (`pethospital.exe`) 已启动
- 检查端口是否正确（默认 8080；不一致时设置 `PET_HOSPITAL_API_BASE`）

### 2. 依赖缺失

```
ModuleNotFoundError: No module named 'mcp'
```

**解决**:
```bash
pip install "mcp[cli]>=2.0.0"
```

### 3. 端口被占用

```
OSError: [Errno 98] Address already in use
```

**解决**:
```bash
# 查找占用端口的进程
netstat -ano | findstr :8000

# 终止进程或更换端口
export MCP_PORT=8001
python server.py
```

## 参考资源

- [MCP Python SDK v2 文档](https://py.sdk.modelcontextprotocol.io/)
- [MCP 2026-07-28 规范](https://modelcontextprotocol.io/specification/2026-07-28)
- [宠物医院 REST API 文档](../README.md)
