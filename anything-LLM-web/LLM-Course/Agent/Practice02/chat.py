"""基于 Practice01 的多轮对话，并新增 function call 工具 getDate 与 isLeapYear。

与 Practice01 的差异：
1. 请求体携带 tools 定义，向模型声明两个工具：
   - getDate：获取当前电脑的日期和时间（通过 subprocess 调用本地 date 命令，不用 datetime）；
   - isLeapYear：计算输入的年份是否是闰年（用 Python 代码完成计算）；
2. 工具调用支持两种方式：模型返回原生 tool_calls 时直接执行；对于不支持原生
   function call 的小模型（如本课程使用的 deepseek-r1-distill-qwen-1.5b），
   从其回复文本中解析 {"name": ..., "arguments": ...} JSON 作为工具调用；
3. 工具结果以 role="tool" 消息回传，再次请求模型，直到给出最终回答。

仍使用非流式请求（stream=False），完整回答以打字机效果逐字输出；仅按 Ctrl+C 退出。
"""

import configparser
import json
import os
import re
import subprocess
import sys
import time

import requests

CONFIG_FILE = "config.ini"
TIMEOUT = 120
TYPE_DELAY = 0.02  # 每个字符的输出间隔（秒）
MAX_TOOL_ROUNDS = 5  # 单轮提问最多连续调用工具的次数，防止模型死循环
TEMPERATURE = 0.2  # 低温让小模型更稳定地按约定输出工具调用 JSON

# 需要走工具的问题的简单识别（时间类 / 闰年类）：命中则要求模型必须调用对应工具
TIME_HINT = re.compile(
    r"几点|几号|日期|时间|今天|现在|闰年|leap|date|time", re.IGNORECASE
)

# 闰年类问题：模型必须调用 isLeapYear 工具
LEAP_HINT = re.compile(r"闰年|leap", re.IGNORECASE)

# 小模型常输出全角标点写 JSON，解析前先归一化成半角
FULLWIDTH_TRANS = str.maketrans(
    {
        "“": '"',  # “
        "”": '"',  # ”
        "‘": "'",  # ‘
        "’": "'",  # ’
        "：": ":",  # ：
        "，": ",",  # ，
    }
)

SYSTEM_PROMPT = (
    "你是对话助手。请严格根据对话历史中的信息回答当前问题，"
    "保持前后一致，不要编造历史中不存在的内容，也不要重复输出固定的介绍话术。\n"
    "当前可用工具：\n"
    "1. getDate（无参数）：获取当前电脑的日期和时间；\n"
    "2. isLeapYear（参数 year，整数）：计算输入年份是否是闰年。\n"
    "当用户询问日期、时间或某年是否是闰年时，你不要自己编造答案，必须调用对应工具：\n"
    "若接口支持原生 function call（tools 声明），使用原生 tool_calls 调用；\n"
    "否则只输出一行 JSON 来调用工具，例如 "
    "{\"name\": \"getDate\", \"arguments\": {}} 或 "
    "{\"name\": \"isLeapYear\", \"arguments\": {\"year\": 2024}}，"
    "该行之外不要输出任何其他文字。\n"
    "收到 role=tool 的工具结果或【工具执行结果】后，直接根据结果用中文简洁回答用户，"
    "严禁改写、猜测结果，也不要在回复里出现 <||> 之类的特殊符号或模板残片。"
)

# 向模型声明的 function call 工具列表
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间（通过本地 date 命令读取系统时间）",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "计算输入的年份是否是闰年",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    }
                },
                "required": ["year"],
            },
        },
    },
]


def tool_names():
    return [t["function"]["name"] for t in TOOLS]


def parse_text_tool_call(text):
    """从回复文本中解析文本式工具调用，适配不支持原生 tool_calls 的小模型。

    小模型常把 JSON 写成全角引号（“name”），先归一化全角符号再解析；
    扫描文本里的每个 '{'，尝试用 JSON 解码器完整解析出对象，
    若对象含 "name" 且命中已注册工具名，则视为一次工具调用，返回 (名称, 参数字典)。
    """
    text = (text or "").translate(FULLWIDTH_TRANS)
    decoder = json.JSONDecoder()
    names = set(tool_names())
    for match in re.finditer(r"\{", text or ""):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name") in names:
            arguments = obj.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            return obj["name"], arguments
    return None


def load_config(path=CONFIG_FILE):
    """读取 ini 配置，返回 [llm] 分区。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    if not cfg.has_section("llm"):
        raise ValueError(f"配置文件缺少 [llm] 分区：{path}")
    return cfg["llm"]


def get_date():
    """getDate 工具实现：调用本地 date 命令获取系统日期和时间。

    Windows 下执行 `date /t` 与 `time /t`（date 命令无参数会进入交互式输入，
    因此加 /t 只打印当前值）；其它平台执行 `date` 命令并带上格式化参数。
    全程通过 subprocess 调用系统命令，不使用 Python 的 datetime。
    """
    if os.name == "nt":
        commands = [["cmd", "/c", "date", "/t"], ["cmd", "/c", "time", "/t"]]
    else:
        commands = [["date", "+%Y-%m-%d %H:%M:%S"]]

    outputs = []
    for cmd in commands:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",  # 命令输出编码与系统不一致时避免崩溃
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"执行命令 {' '.join(cmd)} 失败：{stderr or result.returncode}")
        line = (result.stdout or "").strip()
        if line:
            outputs.append(line)

    if not outputs:
        raise RuntimeError("date 命令没有返回内容")
    return " ".join(outputs)


def is_leap_year(year):
    """isLeapYear 工具实现：用 Python 代码计算某年是否是闰年。

    闰年规则：普通年份能被 4 整除但不能被 100 整除；世纪年份（整百年）能被 400 整除。
    """
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def call_tool(name, arguments):
    """按工具名分发执行，返回给模型的字符串结果。"""
    if name == "getDate":
        try:
            return get_date()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return f" getDate 工具执行失败：{exc}"
    if name == "isLeapYear":
        year = (arguments or {}).get("year")
        try:
            leap = is_leap_year(year)
        except (TypeError, ValueError):
            return f"isLeapYear 参数错误：year 必须是整数年份，收到的是 {year!r}"
        return f"{int(year)} 年{'是' if leap else '不是'}闰年"
    return f"未知工具：{name}"


def ask_llm(messages, conf):
    """向 OpenAI 兼容接口发起一次非流式请求，返回完整响应中的 message 字典。

    messages 为完整对话历史（含本轮提问），保证回答能参考之前的上下文；
    message 可能带有 content、reasoning_content 或 tool_calls。
    """
    url = conf["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {conf['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": conf["model"],
        "messages": messages,  # 携带全部历史，实现多轮上下文
        "tools": TOOLS,  # 声明 function call 工具
        "stream": False,  # 禁用流式，一次性返回完整结果
        "temperature": TEMPERATURE,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def extract_content(message):
    """取出正文：DeepSeek-R1 等思考模型可能把正文放在 reasoning_content。"""
    return message.get("content") or message.get("reasoning_content") or ""


def print_typing(text, delay=TYPE_DELAY):
    """打字机效果：逐字输出完整回答，换行符做短暂停顿。"""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay if ch != "\n" else delay * 5)
    sys.stdout.write("\n")


def normalize_tool_calls(message):
    """把模型回复统一成工具调用列表 [{"id", "name", "arguments"(dict)}]。

    优先取原生 tool_calls（支持 function call 的模型）；
    否则从回复文本中解析文本式 JSON 工具调用（小模型兜底）。
    """
    calls = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") or {}
        raw_args = func.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except ValueError:
            arguments = {}
        calls.append(
            {
                "id": call.get("id", ""),
                "name": func.get("name", ""),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    if calls:
        return calls

    parsed = parse_text_tool_call(extract_content(message))
    if parsed:
        name, arguments = parsed
        return [{"id": f"txt-{name}", "name": name, "arguments": arguments}]
    return []


def run_turn(prompt, messages, conf):
    """处理一次提问：请求模型 → 若要求调用工具则执行并回传结果 → 循环直到拿到最终回答。

    返回 (最终回答文本, 本轮追加到历史的消息列表)。
    """
    turn_messages = [{"role": "user", "content": prompt}]
    final_answer = ""
    nudged = False  # 时间类问题若模型未调用工具，是否已追问过一次
    tool_results = []  # 本轮已执行工具的 (名称, 结果)，用于兜底生成答案

    def fallback_answer():
        """小模型始终没给出可用回答时，直接用工具结果作为答案。

        闰年类问题优先使用 isLeapYear 的结果，避免误用 getDate 的结果。
        """
        if not tool_results:
            return ""
        if LEAP_HINT.search(prompt):
            for name, result in reversed(tool_results):
                if name == "isLeapYear":
                    return f"根据 {name} 工具的返回结果：{result}"
        name, result = tool_results[-1]
        return f"根据 {name} 工具的返回结果：{result}"

    for _ in range(MAX_TOOL_ROUNDS):
        message = ask_llm(messages + turn_messages, conf)
        tool_calls = normalize_tool_calls(message)

        if not tool_calls:
            if not nudged and TIME_HINT.search(prompt):
                # 小模型可能漏调工具：追问一次，要求它先调用对应工具再回答
                nudged = True
                if LEAP_HINT.search(prompt):
                    year_match = re.search(r"(\d{3,4})", prompt)
                    year = year_match.group(1) if year_match else "2000"
                    nudge_text = (
                        f"请不要直接回答，先调用 isLeapYear 工具计算 {year} 年是否是闰年，"
                        "只输出工具调用，例如 "
                        '{"name": "isLeapYear", "arguments": {"year": %s}}，不要输出其他文字。' % year
                    )
                else:
                    nudge_text = (
                        "请不要直接回答，先调用 getDate 工具获取当前日期和时间，"
                        "只输出工具调用，不要输出其他文字。"
                    )
                turn_messages.append(
                    {"role": "assistant", "content": message.get("content") or ""}
                )
                turn_messages.append({"role": "user", "content": nudge_text})
                continue
            final_answer = extract_content(message)
            turn_messages.append(
                {"role": "assistant", "content": message.get("content") or ""}
            )
            break

        # 模型要求调用工具：记录 assistant 回复，再逐个执行并以 role="tool" 回传结果
        if tool_results and all(
            c["name"] == tool_results[-1][0] for c in tool_calls
        ) and not (LEAP_HINT.search(prompt) and tool_results[-1][0] != "isLeapYear"):
            # 已拿到过工具结果，模型却在重复调用同一工具：直接用结果收尾
            final_answer = fallback_answer()
            turn_messages.append(
                {"role": "assistant", "content": final_answer}
            )
            break

        turn_messages.append(
            {"role": "assistant", "content": message.get("content") or ""}
        )
        for call in tool_calls:
            print(f"\n[调用工具] {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
            try:
                result = call_tool(call["name"], call["arguments"])
            except Exception as exc:  # 工具异常也要回传给模型，让模型自行解释
                result = f"{call['name']} 执行异常：{exc}"
            print(f"[工具结果] {result}")
            tool_results.append((call["name"], result))
            if call["id"].startswith("txt-"):
                # 文本式工具调用：用 user 消息回传结果，避免小模型触发原生工具模板
                turn_messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"【工具 {call['name']} 的执行结果】{result}\n"
                            "请仅根据该结果用中文简洁回答，严禁改写、猜测或编造。"
                        ),
                    }
                )
            else:
                turn_messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )

        if LEAP_HINT.search(prompt) and not any(
            name == "isLeapYear" for name, _ in tool_results
        ):
            # 闰年类问题模型却调了别的工具：追问一次，要求它调用 isLeapYear
            turn_messages.append(
                {
                    "role": "user",
                    "content": "请调用 isLeapYear 工具计算该年份是否是闰年，"
                               "只输出工具调用 JSON，例如 "
                               "{\"name\": \"isLeapYear\", \"arguments\": {\"year\": 2000}}。",
                }
            )
            continue
    else:
        final_answer = fallback_answer()
        if not final_answer:
            print("（工具调用次数达到上限，未取得最终回答）", file=sys.stderr)

    # 合理性校验：闰年问题已调用 isLeapYear，但模型的回答完全没提到闰年结论，视为无效回答
    if (
        final_answer
        and tool_results
        and LEAP_HINT.search(prompt)
        and any(name == "isLeapYear" for name, _ in tool_results)
        and "闰年" not in final_answer
    ):
        final_answer = fallback_answer()

    return final_answer, turn_messages


def main():
    try:
        conf = load_config()
    except (OSError, configparser.Error, KeyError) as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        sys.exit(1)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]  # 系统提示词固定放在最前

    try:
        while True:
            prompt = input("请输入问题：").strip()
            if not prompt:
                print("问题不能为空", file=sys.stderr)
                continue

            try:
                print("\n---------- 模型回答 ----------")
                answer, turn_messages = run_turn(prompt, messages, conf)
                if not answer:
                    print("（模型返回为空，未写入历史）", file=sys.stderr)
                    # 工具交互过程仍保留在上下文中，便于模型延续思路
                    messages.extend(turn_messages)
                    continue
                print_typing(answer)
                messages.extend(turn_messages)
            except requests.exceptions.Timeout:
                print(f"请求超时（{TIMEOUT}s）", file=sys.stderr)
            except requests.exceptions.HTTPError as exc:
                print(f"HTTP 错误 {exc.response.status_code}：{exc.response.text}", file=sys.stderr)
            except requests.exceptions.RequestException as exc:  # 含连接失败等网络异常
                print(f"网络请求失败：{exc}", file=sys.stderr)
            except (KeyError, IndexError, ValueError) as exc:
                print(f"返回数据格式异常：{exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\n收到中断信号或输入流结束，退出程序。")


if __name__ == "__main__":
    main()
