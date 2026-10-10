import httpx

# =========【在这里替换成你的API Key】=========
API_KEY = "2QV8DFB-ZF84QYP-NM96ZJQ-4C01N98"
# ============================================

url = "http://localhost:3001/api/v1/workspaces"

headers = {
    "Authorization": f"Bearer {API_KEY.strip()}", # strip自动清除首尾空格
    "Content-Type": "application/json"
}

print(f"当前API_KEY：|{API_KEY.strip()}|") # 打印密钥，检查有没有多余字符
try:
    resp = httpx.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    print("✅连接成功！工作区列表：")
    print(resp.json())
except Exception as e:
    print("❌连接失败：", e)