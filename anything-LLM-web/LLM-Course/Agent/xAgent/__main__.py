"""xAgent 命令行入口。

用法：
- ``python -m xAgent``  启动 TUI
- ``python -m xAgent --cli``  启动 CLI（直接复用 core，不做额外装饰）
"""

from __future__ import annotations

import argparse
import sys

from .core import Agent, OpenAICompatibleClient, build_default_registry, load_config
from .core.utils.text import preview_text
from .tui import run as run_tui


def run_cli() -> None:
    """简易 CLI：复用 core Agent，便于排查问题或在无 TUI 环境使用。"""
    try:
        config = load_config()
    except Exception as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        sys.exit(1)

    registry = build_default_registry(config.agent, config.search)
    client = OpenAICompatibleClient(config.llm)
    agent = Agent(client, registry, config.agent)

    print("xAgent CLI 已启动（输入 Ctrl+C 退出）")
    print(f"模型：{config.llm.model} | 工具数：{len(registry)}")
    print("-" * 40)

    try:
        while True:
            try:
                prompt = input("请输入问题：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not prompt:
                print("问题不能为空", file=sys.stderr)
                continue

            print("\n[思考中...]")
            answer_text = ""
            try:
                for event in agent.send(prompt):
                    from .core import (
                        AnswerReady,
                        ArgumentRepaired,
                        NudgeIssued,
                        ToolCallFinished,
                        ToolCallStarted,
                        TurnFailed,
                    )

                    if isinstance(event, ToolCallStarted):
                        import json

                        print(
                            f"  [工具] {event.name}({json.dumps(event.arguments, ensure_ascii=False)})"
                        )
                    elif isinstance(event, ToolCallFinished):
                        icon = "✓" if event.ok else "✗"
                        print(f"    {icon} {preview_text(event.result, limit=200)}")
                    elif isinstance(event, ArgumentRepaired):
                        print(f"  [修正] {event.detail}")
                    elif isinstance(event, NudgeIssued):
                        print(f"  [追问] {event.prompt}")
                    elif isinstance(event, AnswerReady):
                        answer_text = event.text
                    elif isinstance(event, TurnFailed):
                        print(f"错误：{event.message}", file=sys.stderr)
            except Exception as exc:
                print(f"错误：{exc}", file=sys.stderr)
                continue

            print("\n---------- 回答 ----------")
            print(answer_text or "（无回答）")
            print()
    except KeyboardInterrupt:
        print("\n已退出")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="xAgent")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="使用命令行模式（而不是 TUI）",
    )
    args = parser.parse_args(argv)
    if args.cli:
        run_cli()
    else:
        run_tui()


if __name__ == "__main__":
    main()
