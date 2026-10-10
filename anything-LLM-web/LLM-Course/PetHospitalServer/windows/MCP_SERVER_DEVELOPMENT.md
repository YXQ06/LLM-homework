# 宠物医院 MCP Server 开发指南

## 项目概述

基于宠物医院 REST API 开发 MCP (Model Context Protocol) Server，供其他 Agent MCP Client 消费。

**目标**：将宠物医院的 REST API 接口封装为 MCP Tools，支持 2026-07-28 规范。

## 技术栈

- **语言**：Python 3.10+
- **MCP SDK**：`mcp[cli]` (v2)
- **协议版本**：MCP 2026-07-28 (无状态)
- **传输方式**：Streamable HTTP (推荐) / STDIO

## 开发计划

### 阶段1: MVP (当前)
- [x] `list_pets` - 宠物列表查询
- [x] 支持多种筛选参数
- [x] 格式化输出

### 阶段2: 基础CRUD
- [x] `get_pet` - 获取单个宠物详情
- [x] `create_pet` - 创建宠物档案
- [x] `update_pet` - 更新宠物档案
- [x] `delete_pet` - 删除宠物档案

### 阶段3: 病历和收费
- [x] `get_pet_records` - 获取病历
- [x] `add_pet_record` - 添加病历
- [x] `get_pet_charges` - 获取消费明细
- [x] `add_pet_charge` - 添加收费

### 阶段4: 高级功能
- [x] `search_pets` - 全文检索
- [x] `get_stats` - 统计信息
- [x] `export_data` - 数据导出

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
mcp_mvp/
├── server.py              # MCP Server 主程序
├── test_client.py         # 测试客户端
├── requirements.txt       # 依赖列表
├── start.bat              # Windows 启动脚本
└── README.md
```

### 3. 运行

```bash
# 启动宠物医院 REST API
./pethospital.exe

# 启动 MCP Server
python server.py
```

## MCP 工具清单

### 阶段1: MVP (1 个工具)
1. `list_pets` - 宠物列表查询 (支持筛选、排序、分页)

### 阶段2: 基础CRUD (4 个工具)
2. `get_pet` - 获取单个宠物详情
3. `create_pet` - 创建宠物档案
4. `update_pet` - 更新宠物档案
5. `delete_pet` - 删除宠物档案

### 阶段3: 病历和收费 (4 个工具)
6. `get_pet_records` - 获取病历记录
7. `add_pet_record` - 添加病历记录
8. `get_pet_charges` - 获取消费明细
9. `add_pet_charge` - 添加收费记录

### 阶段4: 高级功能 (3 个工具)
10. `search_pets` - 全文检索
11. `get_stats` - 经营统计
12. `export_data` - 数据导出

**已完成：12/12 个 MCP Tools**

## 参考资源

- [MCP Python SDK v2 文档](https://py.sdk.modelcontextprotocol.io/)
- [MCP 2026-07-28 规范](https://modelcontextprotocol.io/specification/2026-07-28)
- [宠物医院 REST API 文档](./README.md)
- [MVP 实现代码](./mcp_mvp/)
