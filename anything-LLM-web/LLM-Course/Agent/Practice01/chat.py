"""与 LLM 进行多轮循环对话：读取 config.ini 配置，持续接收提问并打印完整回答。

不使用流式请求（stream=False），一次请求拿到完整结果后，再以打字机效果逐字输出。
每轮把历史聊天记录 history 与本次提问拼接后发给 LLM，实现多轮上下文记忆；
仅按下 Ctrl+C 时退出程序。
"""

import configparser
import os
import sys
import time

import requests

CONFIG_FILE = "config.ini"
TIMEOUT = 120
TYPE_DELAY = 0.02  # 每个字符的输出间隔（秒）

SYSTEM_PROMPT = (
    "你是对话助手。请严格根据对话历史中的信息回答当前问题，"
    "保持前后一致，不要编造历史中不存在的内容，也不要重复输出固定的介绍话术。"
)


def load_config(path=CONFIG_FILE):
    """读取 ini 配置，返回 [llm] 分区。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    if not cfg.has_section("llm"):
        raise ValueError(f"配置文件缺少 [llm] 分区：{path}")
    return cfg["llm"]


def ask_llm(messages, conf):
    """向 OpenAI 兼容接口发起一次非流式请求，返回完整回答文本。

    messages 为完整对话历史（含本轮提问），保证回答能参考之前的上下文。
    """
    url = conf["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {conf['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": conf["model"],
        "messages": messages,  # 携带全部历史，实现多轮上下文
        "stream": False,  # 禁用流式，一次性返回完整结果
        "temperature": 0.7,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    message = resp.json()["choices"][0]["message"]
    # DeepSeek-R1 等思考模型可能把正文放在 reasoning_content，content 为空时回退取它
    return message.get("content") or message.get("reasoning_content") or ""


def print_typing(text, delay=TYPE_DELAY):
    """打字机效果：逐字输出完整回答，换行符做短暂停顿。"""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay if ch != "\n" else delay * 5)
    sys.stdout.write("\n")


def main():
    try:
        conf = load_config()
    except (OSError, configparser.Error, KeyError) as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        sys.exit(1)

    history = []  # 完整对话历史，每轮都会随本次提问一起发送给 LLM
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]  # 系统提示词固定放在最前

    try:
        while True:
            prompt = input("请输入问题：").strip()
            if not prompt:
                print("问题不能为空", file=sys.stderr)
                continue

            try:
                print("\n---------- 模型回答 ----------")
                answer = ask_llm(messages + [{"role": "user", "content": prompt}], conf)
                if not answer:
                    print("（模型返回为空，未写入历史）", file=sys.stderr)
                    continue
                print_typing(answer)
                messages.append({"role": "user", "content": prompt})
                messages.append({"role": "assistant", "content": answer})
            except requests.exceptions.Timeout:
                print(f"请求超时（{TIMEOUT}s）", file=sys.stderr)
            except requests.exceptions.HTTPError as exc:
                print(f"HTTP 错误 {exc.response.status_code}：{exc.response.text}", file=sys.stderr)
            except requests.exceptions.RequestException as exc:  # 含连接失败等网络异常
                print(f"网络请求失败：{exc}", file=sys.stderr)
            except (KeyError, IndexError, ValueError) as exc:
                print(f"返回数据格式异常：{exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n收到中断信号，退出程序。")


if __name__ == "__main__":
    main()
