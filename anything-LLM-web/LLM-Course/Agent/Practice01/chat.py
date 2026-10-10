"""与 LLM 进行一次性对话：读取 config.ini 配置，发送单次提问，打印完整回答。

不使用流式请求（stream=False），一次请求拿到完整结果后，再以打字机效果逐字输出。
"""

import configparser
import os
import sys
import time

import requests

CONFIG_FILE = "config.ini"
TIMEOUT = 120
TYPE_DELAY = 0.02  # 每个字符的输出间隔（秒）


def load_config(path=CONFIG_FILE):
    """读取 ini 配置，返回 [llm] 分区。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    if not cfg.has_section("llm"):
        raise ValueError(f"配置文件缺少 [llm] 分区：{path}")
    return cfg["llm"]


def ask_llm(question, conf):
    """向 OpenAI 兼容接口发起一次非流式请求，返回完整回答文本。"""
    url = conf["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {conf['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": conf["model"],
        "messages": [{"role": "user", "content": question}],  # 只发一轮，不带历史
        "stream": False,  # 禁用流式，一次性返回完整结果
        "temperature": 0.7,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


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

    question = input("请输入问题：").strip()
    if not question:
        print("问题不能为空", file=sys.stderr)
        sys.exit(1)

    try:
        print("\n模型回答：")
        print_typing(ask_llm(question, conf))
    except requests.exceptions.Timeout:
        print(f"请求超时（{TIMEOUT}s）", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        print(f"HTTP 错误 {exc.response.status_code}：{exc.response.text}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as exc:  # 含连接失败等网络异常
        print(f"网络请求失败：{exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, IndexError, ValueError) as exc:
        print(f"返回数据格式异常：{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
