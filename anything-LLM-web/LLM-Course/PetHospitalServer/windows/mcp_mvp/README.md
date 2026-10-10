# 🐾 宠物医院 MCP Server - MVP 版本

这是宠物医院 MCP Server 的最小可用版本，只实现了 `list_pets` 功能。

## 功能特性

- ✅ 获取宠物档案列表
- ✅ 支持按物种、医生、状态筛选
- ✅ 支持关键词搜索
- ✅ 支持按花费区间筛选
- ✅ 支持排序和分页

## 快速开始

### 前置条件

1. **Python 3.10+**
2. **宠物医院 REST API** (pethospital.exe)

### 安装和运行

#### 方式1: 使用启动脚本 (Windows)

```bash
# 双击运行
start.bat
```

#### 方式2: 手动运行

```bash
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 确保宠物医院 API 已启动
./pethospital.exe

# 4. 启动 MCP Server
python server.py
```

#### 方式3: 使用 uv (推荐)

```bash
# 安装 uv (如果未安装)
pip install uv

# 运行 MCP Server
uv run --with "mcp[cli]>=2.0.0" --with httpx server.py
```

## 测试

### 使用测试客户端

```bash
# 在另一个终端运行
python test_client.py
```

### 使用 MCP Inspector

```bash
# 安装 MCP Inspector
npx -y @modelcontextprotocol/inspector

# 在浏览器中打开显示的 URL
# 连接到 http://localhost:8000/mcp
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

## 支持的筛选参数

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
mcp_mvp/
├── server.py          # MCP Server 主程序
├── test_client.py     # 测试客户端
├── requirements.txt   # 依赖列表
├── start.bat          # Windows 启动脚本
└── README.md          # 说明文档
```

## 故障排查

### 1. 连接失败

```
错误: Connection refused
```

**解决**:
- 确保宠物医院 REST API 已启动
- 检查端口是否正确 (默认 8080)

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

## 下一步

完成 MVP 验证后，可以按以下顺序添加功能：

1. `get_pet` - 获取单个宠物详情
2. `create_pet` - 创建宠物档案
3. `update_pet` - 更新宠物档案
4. `delete_pet` - 删除宠物档案
5. `get_pet_records` - 获取病历
6. `add_pet_record` - 添加病历
7. `get_pet_charges` - 获取消费明细
8. `add_pet_charge` - 添加收费
9. `get_stats` - 获取统计信息

## 参考资源

- [MCP Python SDK v2 文档](https://py.sdk.modelcontextprotocol.io/)
- [MCP 2026-07-28 规范](https://modelcontextprotocol.io/specification/2026-07-28)
- [宠物医院 REST API 文档](../README.md)
