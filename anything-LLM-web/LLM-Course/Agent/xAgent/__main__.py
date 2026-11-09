"""xAgent 命令行入口。

用法：
- ``python -m xAgent``  启动 TUI
- ``python -m xAgent --cli``  启动 CLI（直接复用 core，不做额外装饰）
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .core import (
    Agent,
    CompactionFailed,
    CompactionFinished,
    CompactionStarted,
    ContextUsage,
    OpenAICompatibleClient,
    SessionStore,
    build_default_registry,
    ensure_config,
    load_config,
)
from .core.mcp import create_mcp_manager, register_mcp_tools
from .core.utils.text import preview_text
from .tui import run as run_tui
from .tui.commands import format_command_help


def _print_help() -> None:
    print(format_command_help())


def run_cli() -> None:
    """简易 CLI：复用 core Agent，便于排查问题或在无 TUI 环境使用。"""
    try:
        config_path, created = ensure_config()
    except Exception as exc:
        print(f"配置初始化失败：{exc}", file=sys.stderr)
        sys.exit(1)

    if created:
        print(f"已在当前目录生成配置文件：{config_path}")
        print("请修改 [llm] 下的 base_url、model、api_key，然后重新启动 xAgent。")
        return

    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        sys.exit(1)

    project_root = os.getcwd()
    registry = build_default_registry(
        config.agent, config.search, project_root=project_root
    )
    mcp_manager = create_mcp_manager(
        config.agent, project_root=project_root
    )
    try:
        register_mcp_tools(registry, mcp_manager)
    except Exception as exc:  # MCP 失败不能影响本地工具
        print(f"MCP 初始化失败：{exc}", file=sys.stderr)
    for line in mcp_manager.startup_messages():
        print(line)
    client = OpenAICompatibleClient(config.llm)
    agent = Agent(
        client, registry, config.agent, project_root=project_root
    )

    session_dir = Path(config.agent.session_dir or ".agents/sessions")
    if not session_dir.is_absolute():
        session_dir = Path(project_root) / session_dir
    session_store = SessionStore(session_dir)
    session_id = session_store.new_session_id()
    session_title = ""
    session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def persist(title: str = "") -> Path | None:
        nonlocal session_title
        if len(agent.messages) <= 1:
            return None
        if title:
            session_title = title.strip()
        try:
            return session_store.save(
                session_id,
                agent.messages,
                title=session_title,
                created_at=session_created_at,
                project_root=project_root,
                agents_md_path=agent.agents_md_path,
                agents_md_hash=agent.agents_md_hash,
            )
        except Exception as exc:
            print(f"保存会话失败：{exc}", file=sys.stderr)
            return None

    def inject_agents_md() -> None:
        try:
            document = agent.inject_project_instructions(
                Path(project_root) / config.agent.agents_md_filename,
                max_chars=config.agent.agents_md_max_chars,
            )
        except Exception as exc:
            print(f"读取 AGENTS.md 失败：{exc}", file=sys.stderr)
            return
        if document is not None:
            print(
                f"已注入项目说明 {document.path.name}"
                f"（{len(document.text)} 字符），本会话后续直接复用。"
            )
            persist()

    def load_session(value: str) -> None:
        nonlocal session_id, session_title, session_created_at
        try:
            record = session_store.load(value)
        except Exception as exc:
            print(f"恢复会话失败：{exc}", file=sys.stderr)
            return
        agent.restore_messages(
            record.messages,
            agents_md_path=record.agents_md_path,
            agents_md_hash=record.agents_md_hash,
        )
        session_id = record.id
        session_title = record.title
        session_created_at = record.created_at
        print(
            f"已恢复会话：{record.title}"
            f"（{len(record.messages)} 条消息，更新于 {record.updated_at}）"
        )

    inject_agents_md()

    print("xAgent CLI 已启动（输入 Ctrl+C 退出，/help 查看命令）")
    print(f"模型：{config.llm.model} | 工具数：{len(registry)}")
    print("-" * 40)

    try:
        while True:
            snapshot = agent.context_snapshot(resolve_window=True)
            default_text = "（默认）" if snapshot.window_is_default else ""
            print(
                f"[上下文] {snapshot.used_tokens / 1000:.1f}k / "
                f"{snapshot.window_tokens / 1000:.1f}k{default_text}"
                f" | 已占用 {snapshot.percent:.1f}%"
            )
            try:
                prompt = input("请输入问题（/help 查看命令）：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not prompt:
                print("问题不能为空", file=sys.stderr)
                continue

            if prompt.startswith("/"):
                token, _, args = prompt.partition(" ")
                token = token.casefold()
                args = args.strip()
                if token == "/help":
                    _print_help()
                elif token == "/new":
                    persist()
                    agent.reset()
                    session_id = session_store.new_session_id()
                    session_title = args
                    session_created_at = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    print(f"已开始新会话：{session_id}")
                    inject_agents_md()
                elif token == "/save":
                    path = persist(args)
                    if path is None:
                        print("当前没有可保存的对话。")
                    else:
                        print(f"会话已保存：{path}")
                elif token in ("/resume", "/history"):
                    if args:
                        persist()
                        load_session(args)
                    else:
                        sessions = session_store.list_sessions(
                            config.agent.session_max_history
                        )
                        if not sessions:
                            print("还没有历史会话。")
                        else:
                            print("历史会话（使用 /resume <编号> 恢复）：")
                            for item in sessions:
                                print(f"  {item.id}  {item.preview}")
                elif token == "/compact":
                    for event in agent.compact():
                        if isinstance(event, CompactionStarted):
                            print(
                                f"[压缩] 正在归档 {event.message_count} 条消息，"
                                f"归档编号 {event.archive_id}"
                            )
                        elif isinstance(event, CompactionFinished):
                            print(
                                f"[压缩] 完成，归档编号 {event.archive_id}，"
                                f"压缩后约 {event.compacted_tokens / 1000:.1f}k Tokens"
                            )
                        elif isinstance(event, CompactionFailed):
                            print(f"[压缩失败] {event.message}", file=sys.stderr)
                            print("原有上下文已保留，可重新执行 /compact。")
                    if config.agent.session_auto_save_after_turn:
                        persist()
                elif token == "/context":
                    print(
                        f"当前 {snapshot.used_tokens / 1000:.1f}k / "
                        f"{snapshot.window_tokens / 1000:.1f}k"
                        f"{default_text}，已占用 {snapshot.percent:.1f}%；"
                        f"压缩阈值 {snapshot.warn_ratio * 100:.0f}%。"
                    )
                elif token == "/tools":
                    for spec in registry.specs:
                        print(f"  {spec.name}: {spec.description}")
                elif token in ("/quit", "/exit"):
                    if config.agent.session_auto_save_on_exit:
                        persist()
                    break
                elif token == "/clear":
                    print("\n" * 40)
                else:
                    print(f"未知命令：{token}；输入 /help 查看可用命令。")
                continue

            if agent.needs_compaction():
                print(
                    "上下文已达到压缩阈值，请先执行 /compact 压缩上下文。",
                    file=sys.stderr,
                )
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

                    if isinstance(event, ContextUsage):
                        if event.warning:
                            print(f"  [上下文警告] {event.warning}", file=sys.stderr)
                    elif isinstance(event, ToolCallStarted):
                        import json

                        print(
                            f"  [工具] {event.name}("
                            f"{json.dumps(event.arguments, ensure_ascii=False)})"
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
            if config.agent.session_auto_save_after_turn:
                persist()
    except KeyboardInterrupt:
        if config.agent.session_auto_save_on_exit:
            persist()
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
