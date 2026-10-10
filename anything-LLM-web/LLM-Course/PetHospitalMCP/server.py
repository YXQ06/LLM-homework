"""
宠物医院 MCP Server
封装宠物医院 REST API 为 MCP Tools，供其他 Agent MCP Client 消费
"""

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

# 创建 MCP Server
mcp = MCPServer("pet-hospital-mcp")

# REST API 配置
REST_API_BASE = "http://127.0.0.1:8080"

# 合法枚举值（用于输入校验）
VALID_SPECIES = {"犬", "猫", "兔", "仓鼠", "鹦鹉", "龟", "马"}
VALID_STATUS = {"待就诊", "就诊中", "住院中", "已康复", "慢性病随访"}
VALID_GENDER = {"公", "母"}


async def api_request(method: str, path: str, params: dict = None, json_data: dict = None) -> dict:
    """通用 REST API 请求"""
    async with httpx.AsyncClient() as client:
        try:
            url = f"{REST_API_BASE}{path}"
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
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
        readOnlyHint=True,
        openWorldHint=False
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
    result = await api_request("GET", "/api/v1/pets", params=params)

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


def format_pet_detail(pet: dict) -> str:
    """格式化单个宠物详情"""
    lines = []
    lines.append(f"=== 宠物详情 ===\n")
    lines.append(f"ID: {pet.get('id', 'N/A')}")
    lines.append(f"姓名: {pet.get('name', 'N/A')}")
    lines.append(f"物种: {pet.get('species', 'N/A')}")
    lines.append(f"品种: {pet.get('breed', 'N/A')}")
    lines.append(f"性别: {pet.get('gender', 'N/A')}")
    lines.append(f"月龄: {pet.get('ageMonths', 'N/A')}")
    lines.append(f"毛色: {pet.get('color', 'N/A')}")
    lines.append(f"芯片号: {pet.get('chipNumber', 'N/A')}")
    lines.append(f"主人: {pet.get('ownerName', 'N/A')}")
    lines.append(f"电话: {pet.get('ownerPhone', 'N/A')}")
    lines.append(f"住址: {pet.get('ownerAddress', 'N/A')}")
    lines.append(f"主治医生: {pet.get('doctor', 'N/A')}")
    lines.append(f"疾病: {pet.get('disease', 'N/A')}")
    lines.append(f"状态: {pet.get('status', 'N/A')}")
    lines.append(f"过敏史: {pet.get('allergies', 'N/A')}")
    lines.append(f"备注: {pet.get('notes', 'N/A')}")
    lines.append(f"创建时间: {pet.get('createdAt', 'N/A')}")
    lines.append(f"更新时间: {pet.get('updatedAt', 'N/A')}")
    lines.append(f"\n总花费: ¥{pet.get('totalCost', 0):.2f}")
    lines.append(f"就诊次数: {pet.get('visitCount', 0)}")
    
    # 病历记录
    records = pet.get("records", [])
    if records:
        lines.append(f"\n--- 历史病历 ({len(records)} 条) ---")
        for i, record in enumerate(records, 1):
            lines.append(f"{i}. {record.get('date', 'N/A')} - {record.get('diagnosis', 'N/A')}")
            lines.append(f"   医生: {record.get('doctor', 'N/A')} | 症状: {record.get('symptoms', 'N/A')}")
            lines.append(f"   治疗: {record.get('treatment', 'N/A')}")
    
    # 消费明细
    charges = pet.get("charges", [])
    if charges:
        lines.append(f"\n--- 消费明细 ({len(charges)} 笔) ---")
        for i, charge in enumerate(charges, 1):
            lines.append(f"{i}. {charge.get('date', 'N/A')} - {charge.get('item', 'N/A')}")
            lines.append(f"   金额: ¥{charge.get('amount', 0):.2f} | 类别: {charge.get('category', 'N/A')}")
    
    return "\n".join(lines)


@mcp.tool(
    name="get_pet",
    description="获取单个宠物的详细信息，包括病历和消费记录",
    annotations=ToolAnnotations(
        title="宠物详情查询",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def get_pet(pet_id: str) -> str:
    """
    获取单个宠物的详细信息

    Args:
        pet_id: 宠物ID (如 PET-000001)

    Returns:
        JSON 格式的宠物详情数据
    """
    result = await api_request("GET", f"/api/v1/pets/{pet_id}")
    
    if result.get("code") == 200 and result.get("data"):
        return format_pet_detail(result["data"])
    else:
        return f"查询失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="create_pet",
    description="创建新的宠物档案",
    annotations=ToolAnnotations(
        title="创建宠物档案",
        readOnlyHint=False,
        openWorldHint=False
    )
)
async def create_pet(
    name: str,
    species: str,
    breed: str = "",
    gender: str = "",
    age_months: int = 0,
    color: str = "",
    chip_number: str = "",
    owner_name: str = "",
    owner_phone: str = "",
    owner_address: str = "",
    doctor: str = "",
    disease: str = "",
    status: str = "",
    allergies: str = "",
    notes: str = ""
) -> str:
    """
    创建新的宠物档案

    Args:
        name: 宠物姓名
        species: 物种 (犬/猫/兔/仓鼠/鹦鹉/龟/马)
        breed: 品种
        gender: 性别 (公/母)
        age_months: 月龄
        color: 毛色
        chip_number: 芯片号
        owner_name: 主人姓名
        owner_phone: 主人电话
        owner_address: 主人住址
        doctor: 主治医生
        disease: 疾病
        status: 就诊状态 (待就诊/就诊中/住院中/已康复/慢性病随访)
        allergies: 过敏史
        notes: 备注

    Returns:
        创建结果
    """
    # 输入校验
    if not name.strip():
        return "创建失败: 宠物姓名不能为空"
    if species not in VALID_SPECIES:
        return f"创建失败: 物种必须是 {', '.join(sorted(VALID_SPECIES))} 中的一种"
    if gender and gender not in VALID_GENDER:
        return f"创建失败: 性别必须是 {', '.join(sorted(VALID_GENDER))} 中的一种"
    if age_months < 0:
        return "创建失败: 月龄不能为负数"
    if status and status not in VALID_STATUS:
        return f"创建失败: 就诊状态必须是 {', '.join(sorted(VALID_STATUS))} 中的一种"

    pet_data = {
        "name": name,
        "species": species
    }
    
    # 添加可选字段
    optional_fields = {
        "breed": breed,
        "gender": gender,
        "ageMonths": age_months,
        "color": color,
        "chipNumber": chip_number,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "ownerAddress": owner_address,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "allergies": allergies,
        "notes": notes
    }
    
    for key, value in optional_fields.items():
        if value:
            pet_data[key] = value
    
    result = await api_request("POST", "/api/v1/pets", json_data=pet_data)
    
    if result.get("code") == 200 or result.get("code") == 201:
        new_pet = result.get("data", {})
        return f"创建成功！宠物ID: {new_pet.get('id', 'N/A')}\n{format_pet_detail(new_pet)}"
    else:
        return f"创建失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="update_pet",
    description="更新宠物档案信息",
    annotations=ToolAnnotations(
        title="更新宠物档案",
        readOnlyHint=False,
        openWorldHint=False
    )
)
async def update_pet(
    pet_id: str,
    name: str = "",
    species: str = "",
    breed: str = "",
    gender: str = "",
    age_months: int = 0,
    color: str = "",
    chip_number: str = "",
    owner_name: str = "",
    owner_phone: str = "",
    owner_address: str = "",
    doctor: str = "",
    disease: str = "",
    status: str = "",
    allergies: str = "",
    notes: str = ""
) -> str:
    """
    更新宠物档案信息

    Args:
        pet_id: 宠物ID (如 PET-000001)
        name: 宠物姓名
        species: 物种
        breed: 品种
        gender: 性别
        age_months: 月龄
        color: 毛色
        chip_number: 芯片号
        owner_name: 主人姓名
        owner_phone: 主人电话
        owner_address: 主人住址
        doctor: 主治医生
        disease: 疾病
        status: 就诊状态
        allergies: 过敏史
        notes: 备注

    Returns:
        更新结果
    """
    pet_data = {}
    
    # 只添加非空字段
    optional_fields = {
        "name": name,
        "species": species,
        "breed": breed,
        "gender": gender,
        "ageMonths": age_months,
        "color": color,
        "chipNumber": chip_number,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "ownerAddress": owner_address,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "allergies": allergies,
        "notes": notes
    }
    
    for key, value in optional_fields.items():
        if value:
            pet_data[key] = value
    
    if not pet_data:
        return "没有提供要更新的字段"
    
    result = await api_request("PATCH", f"/api/v1/pets/{pet_id}", json_data=pet_data)
    
    if result.get("code") == 200:
        updated_pet = result.get("data", {})
        return f"更新成功！\n{format_pet_detail(updated_pet)}"
    else:
        return f"更新失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="delete_pet",
    description="删除宠物档案",
    annotations=ToolAnnotations(
        title="删除宠物档案",
        readOnlyHint=False,
        openWorldHint=False
    )
)
async def delete_pet(pet_id: str) -> str:
    """
    删除宠物档案

    Args:
        pet_id: 宠物ID (如 PET-000001)

    Returns:
        删除结果
    """
    result = await api_request("DELETE", f"/api/v1/pets/{pet_id}")
    
    if result.get("code") == 200:
        return f"删除成功！宠物 {pet_id} 已被移除"
    else:
        return f"删除失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="get_pet_records",
    description="获取宠物的历史病历记录",
    annotations=ToolAnnotations(
        title="获取病历记录",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def get_pet_records(pet_id: str) -> str:
    """
    获取宠物的历史病历记录

    Args:
        pet_id: 宠物ID (如 PET-000001)

    Returns:
        病历记录列表
    """
    result = await api_request("GET", f"/api/v1/pets/{pet_id}/records")
    
    if result.get("code") == 200:
        records = result.get("data", [])
        if not records:
            return f"宠物 {pet_id} 暂无病历记录"
        
        lines = [f"=== 宠物 {pet_id} 病历记录 (共 {len(records)} 条) ===\n"]
        
        for i, record in enumerate(records, 1):
            lines.append(f"【病历 {i}】")
            lines.append(f"  日期: {record.get('date', 'N/A')}")
            lines.append(f"  医生: {record.get('doctor', 'N/A')}")
            lines.append(f"  诊断: {record.get('diagnosis', 'N/A')}")
            lines.append(f"  症状: {record.get('symptoms', 'N/A')}")
            lines.append(f"  治疗: {record.get('treatment', 'N/A')}")
            prescription = record.get('prescription', [])
            if prescription:
                lines.append(f"  处方: {', '.join(prescription)}")
            charge = record.get('charge', 0)
            if charge > 0:
                lines.append(f"  费用: ¥{charge:.2f}")
            lines.append("")
        
        return "\n".join(lines)
    else:
        return f"查询失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="add_pet_record",
    description="为宠物添加一条病历记录",
    annotations=ToolAnnotations(
        title="添加病历记录",
        readOnlyHint=False,
        openWorldHint=False
    )
)
async def add_pet_record(
    pet_id: str,
    doctor: str,
    diagnosis: str,
    symptoms: str = "",
    treatment: str = "",
    prescription: str = "",
    charge: float = 0
) -> str:
    """
    为宠物添加一条病历记录

    Args:
        pet_id: 宠物ID (如 PET-000001)
        doctor: 主治医生
        diagnosis: 诊断结果
        symptoms: 症状描述
        treatment: 治疗方案
        prescription: 处方（多个用逗号分隔）
        charge: 本次就诊费用

    Returns:
        添加结果
    """
    record_data = {
        "doctor": doctor,
        "diagnosis": diagnosis
    }
    
    if symptoms:
        record_data["symptoms"] = symptoms
    if treatment:
        record_data["treatment"] = treatment
    if prescription:
        record_data["prescription"] = [p.strip() for p in prescription.split(",")]
    if charge > 0:
        record_data["charge"] = charge
    
    result = await api_request("POST", f"/api/v1/pets/{pet_id}/records", json_data=record_data)
    
    if result.get("code") == 200 or result.get("code") == 201:
        return f"病历添加成功！宠物 {pet_id}"
    else:
        return f"添加失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="get_pet_charges",
    description="获取宠物的消费明细记录",
    annotations=ToolAnnotations(
        title="获取消费明细",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def get_pet_charges(pet_id: str) -> str:
    """
    获取宠物的消费明细记录

    Args:
        pet_id: 宠物ID (如 PET-000001)

    Returns:
        消费明细列表
    """
    result = await api_request("GET", f"/api/v1/pets/{pet_id}/charges")
    
    if result.get("code") == 200:
        charges = result.get("data", [])
        if not charges:
            return f"宠物 {pet_id} 暂无消费记录"
        
        total = sum(c.get("amount", 0) for c in charges)
        lines = [f"=== 宠物 {pet_id} 消费明细 (共 {len(charges)} 笔，总计 ¥{total:.2f}) ===\n"]
        
        for i, charge in enumerate(charges, 1):
            lines.append(f"【消费 {i}】")
            lines.append(f"  日期: {charge.get('date', 'N/A')}")
            lines.append(f"  项目: {charge.get('item', 'N/A')}")
            lines.append(f"  类别: {charge.get('category', 'N/A')}")
            lines.append(f"  金额: ¥{charge.get('amount', 0):.2f}")
            lines.append(f"  医生: {charge.get('doctor', 'N/A')}")
            lines.append("")
        
        return "\n".join(lines)
    else:
        return f"查询失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="add_pet_charge",
    description="为宠物添加一笔收费记录",
    annotations=ToolAnnotations(
        title="添加收费记录",
        readOnlyHint=False,
        openWorldHint=False
    )
)
async def add_pet_charge(
    pet_id: str,
    item: str,
    category: str,
    amount: float,
    doctor: str = ""
) -> str:
    """
    为宠物添加一笔收费记录

    Args:
        pet_id: 宠物ID (如 PET-000001)
        item: 收费项目
        category: 收费类别 (手术/检查/药品/住院/疫苗/护理/其他)
        amount: 金额
        doctor: 主治医生

    Returns:
        添加结果
    """
    charge_data = {
        "item": item,
        "category": category,
        "amount": amount
    }
    
    if doctor:
        charge_data["doctor"] = doctor
    
    result = await api_request("POST", f"/api/v1/pets/{pet_id}/charges", json_data=charge_data)
    
    if result.get("code") == 200 or result.get("code") == 201:
        return f"收费记录添加成功！宠物 {pet_id}，金额 ¥{amount:.2f}"
    else:
        return f"添加失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="search_pets",
    description="全文检索宠物档案（跨字段搜索，含病历全文）",
    annotations=ToolAnnotations(
        title="全文检索",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def search_pets(
    query: str,
    limit: int = 20
) -> str:
    """
    全文检索宠物档案

    Args:
        query: 搜索关键词（空格分词为AND逻辑）
        limit: 返回结果数量限制（默认20）

    Returns:
        搜索结果列表
    """
    params = {"q": query}
    if limit != 20:
        params["limit"] = limit
    
    result = await api_request("GET", "/api/v1/pets/search", params=params)
    
    if result.get("code") == 200:
        pets = result.get("data", [])
        if not pets:
            return f"未找到与 '{query}' 相关的宠物档案"
        
        lines = [f"=== 搜索结果: '{query}' (共 {len(pets)} 条) ===\n"]
        
        for pet in pets:
            pet_id = pet.get("id", "N/A")
            name = pet.get("name", "N/A")
            species = pet.get("species", "N/A")
            breed = pet.get("breed", "N/A")
            owner = pet.get("ownerName", "N/A")
            doctor = pet.get("doctor", "N/A")
            disease = pet.get("disease", "N/A")
            status = pet.get("status", "N/A")
            
            lines.append(f"【{name}】 {pet_id}")
            lines.append(f"  物种: {species} | 品种: {breed}")
            lines.append(f"  主人: {owner}")
            lines.append(f"  医生: {doctor} | 疾病: {disease}")
            lines.append(f"  状态: {status}")
            lines.append("")
        
        return "\n".join(lines)
    else:
        return f"搜索失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="get_stats",
    description="获取宠物医院经营统计数据",
    annotations=ToolAnnotations(
        title="经营统计",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def get_stats() -> str:
    """
    获取宠物医院经营统计数据

    Returns:
        统计信息摘要
    """
    result = await api_request("GET", "/api/v1/stats")
    
    if result.get("code") == 200:
        stats = result.get("data", {})
        
        lines = [f"=== 宠物医院经营统计 ===\n"]
        
        # 基本统计
        lines.append(f"📊 基本数据:")
        lines.append(f"  宠物档案总数: {stats.get('totalPets', 0)}")
        lines.append(f"  病历记录总数: {stats.get('totalRecords', 0)}")
        lines.append(f"  收费记录总数: {stats.get('totalCharges', 0)}")
        lines.append(f"  总收入: ¥{stats.get('totalRevenue', 0):.2f}")
        lines.append(f"  客单价: ¥{stats.get('averageCostPerPet', 0):.2f}")
        lines.append("")
        
        # 物种分布
        species_stats = stats.get("speciesDistribution", [])
        if species_stats:
            lines.append(f"🐾 物种分布:")
            for item in species_stats:
                lines.append(f"  {item.get('species', 'N/A')}: {item.get('count', 0)} 只")
            lines.append("")
        
        # 医生排行
        doctor_stats = stats.get("doctorRanking", [])
        if doctor_stats:
            lines.append(f"👨‍⚕️ 医生排行:")
            for i, item in enumerate(doctor_stats[:5], 1):
                lines.append(f"  {i}. {item.get('doctor', 'N/A')}: {item.get('count', 0)} 例，收入 ¥{item.get('revenue', 0):.2f}")
            lines.append("")
        
        # 疾病分布
        disease_stats = stats.get("diseaseDistribution", [])
        if disease_stats:
            lines.append(f"🏥 常见疾病:")
            for item in disease_stats[:5]:
                lines.append(f"  {item.get('disease', 'N/A')}: {item.get('count', 0)} 例")
        
        return "\n".join(lines)
    else:
        return f"获取统计失败: {result.get('message', '未知错误')}"


@mcp.tool(
    name="export_data",
    description="导出宠物医院数据",
    annotations=ToolAnnotations(
        title="数据导出",
        readOnlyHint=True,
        openWorldHint=False
    )
)
async def export_data(format: str = "json") -> str:
    """
    导出宠物医院数据

    Args:
        format: 导出格式 (json/csv)

    Returns:
        导出结果信息
    """
    params = {"format": format}
    result = await api_request("GET", "/api/v1/export", params=params)
    
    if result.get("code") == 200:
        data = result.get("data")
        if format.lower() == "csv":
            # CSV格式直接返回内容
            return f"导出成功！\n\n{data}"
        else:
            # JSON格式返回摘要
            pets = data if isinstance(data, list) else []
            return f"导出成功！共 {len(pets)} 条宠物档案记录"
    else:
        return f"导出失败: {result.get('message', '未知错误')}"


# 运行服务器
if __name__ == "__main__":
    print("宠物医院 MCP Server 启动中...")
    print(f"代理 REST API: {REST_API_BASE}")
    print("传输方式: Streamable HTTP")
    print("监听地址: http://localhost:8000/mcp")
    print("\n按 Ctrl+C 停止服务\n")

    mcp.run(transport="streamable-http")
