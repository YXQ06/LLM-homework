"""Agent 核心编排：多轮对话、工具调用、参数容错、漏调兜底、循环检测。

本模块是“界面无关”的业务逻辑，所有对外交互都通过 ``AgentEvent`` 流传递，
TUI、Web、Desktop 都只需要消费这些事件即可。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

from .agents_md import (
    AgentsDocument,
    build_agents_md_message,
    contains_agents_md,
    load_agents_md,
)
from .config import AgentConfig
from .context import (
    ContextManager,
    ContextSnapshot,
    format_messages,
    usage_total_tokens,
)
from .events import (
    AgentEvent,
    AnswerReady,
    ArgumentRepaired,
    CompactionFailed,
    CompactionFinished,
    CompactionStarted,
    ContextUsage,
    NudgeIssued,
    ToolCallFinished,
    ToolCallStarted,
    ToolRun,
    TurnFailed,
    TurnFinished,
    TurnResult,
    TurnStarted,
)
from .llm.client import OpenAICompatibleClient
from .llm.messages import (
    ToolCall,
    assistant_history_message,
    extract_content,
    normalize_tool_calls,
    tool_result_message,
)
from .prompts import build_system_prompt
from .recovery import build_nudge, match_hint, repair_arguments
from .skills import SkillRegistry, build_skills_prompt
from .tools.base import ToolRegistry


class Agent:
    """多轮对话 Agent：工具循环、参数容错、漏调追问、原地打转检测。"""

    def __init__(
        self,
        client: OpenAICompatibleClient,
        registry: ToolRegistry,
        config: AgentConfig,
        system_prompt: str | None = None,
        skills: SkillRegistry | None = None,
        project_root: str | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.config = config
        self.project_root = os.path.abspath(project_root or os.getcwd())

        # 上下文监控、归档和压缩摘要都交给 ContextManager，UI 只消费事件。
        self.context = ContextManager(client, config, self.project_root)
        self._compacting = False
        self._context_warned = False
        self._last_usage_total: int | None = None

        # 以程序启动时的用户工作目录为项目根目录发现 Skill。
        self.skills = (
            skills
            if skills is not None
            else SkillRegistry(
                project_root=project_root,
                max_read_bytes=config.max_read_bytes,
            )
        )
        if "loadSkill" not in registry:
            registry.register(self.skills.tool_spec())
        if "createSkill" not in registry:
            registry.register(self.skills.create_tool_spec())

        self._base_system_prompt = system_prompt
        if system_prompt:
            skills_prompt = build_skills_prompt(self.skills)
            self.system_prompt = (
                system_prompt.rstrip() + "\n\n" + skills_prompt
                if skills_prompt
                else system_prompt
            )
        else:
            self.system_prompt = build_system_prompt(registry, self.skills)
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

        # AGENTS.md 采用“一次注入，全程复用”：保留注入消息，压缩时也保留。
        self.agents_md_injected = False
        self.agents_md_path = ""
        self.agents_md_hash = ""
        self._agents_md_message: dict | None = None

    def inject_project_instructions(
        self,
        path: str | os.PathLike[str],
        *,
        max_chars: int = 200_000,
    ) -> AgentsDocument | None:
        """把项目目录中的 AGENTS.md 注入为一条聊天历史消息。

        同一会话只注入一次；重复调用直接返回 ``None``。注入后的消息会随
        会话一起持久化，后续轮次和上下文压缩都直接复用。
        """
        if self.agents_md_injected:
            return None

        document = load_agents_md(Path(path), max_chars=max_chars)
        if document is None:
            return None

        message = {
            "role": "user",
            "content": build_agents_md_message(document),
        }
        self.messages.append(message)
        self._agents_md_message = dict(message)
        self.agents_md_injected = True
        self.agents_md_path = str(document.path)
        self.agents_md_hash = document.sha256
        self._last_usage_total = None
        return document

    def restore_messages(
        self,
        messages: list[dict] | tuple[dict, ...],
        *,
        agents_md_path: str = "",
        agents_md_hash: str = "",
    ) -> None:
        """用会话文件中的消息恢复对话，保留当前 System Prompt。"""
        restored = [
            dict(message)
            for message in messages
            if message.get("role") != "system"
        ]
        system_message = (
            self.messages[0]
            if self.messages and self.messages[0].get("role") == "system"
            else {"role": "system", "content": self.system_prompt}
        )
        self.messages = [system_message] + restored

        marker_message = next(
            (
                message
                for message in restored
                if contains_agents_md(message.get("content"))
            ),
            None,
        )
        self._agents_md_message = (
            dict(marker_message) if marker_message is not None else None
        )
        self.agents_md_injected = marker_message is not None
        self.agents_md_path = agents_md_path if marker_message is not None else ""
        self.agents_md_hash = agents_md_hash if marker_message is not None else ""
        self._last_usage_total = None
        self._context_warned = False

    def refresh_system_prompt(self) -> None:
        """重建系统提示词并同步到当前对话。

        ``createSkill`` 成功后调用，使新建的 Skill 立即出现在 Skill 清单里，
        无需重启会话。
        """
        if self._base_system_prompt:
            base = self._base_system_prompt.rstrip()
            skills_prompt = build_skills_prompt(self.skills)
            self.system_prompt = (
                base + "\n\n" + skills_prompt if skills_prompt else base
            )
        else:
            self.system_prompt = build_system_prompt(self.registry, self.skills)

        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt
        else:
            self.messages.insert(
                0, {"role": "system", "content": self.system_prompt}
            )
        # 系统提示词变了，之前记录的 usage 不再对应当前消息。
        self._last_usage_total = None

    def reset(self) -> None:
        """清空对话历史，仅保留系统提示词。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._last_usage_total = None
        self._context_warned = False
        self.agents_md_injected = False
        self.agents_md_path = ""
        self.agents_md_hash = ""
        self._agents_md_message = None

    # ------------------------------------------------------------------ 上下文

    def context_snapshot(
        self,
        *,
        extra_messages: list[dict] | None = None,
        usage: dict | None = None,
        resolve_window: bool = True,
    ) -> ContextSnapshot:
        """计算当前上下文使用量。

        ``usage`` 优先使用接口返回的 Token 统计；没有时按消息内容估算，
        并把工具声明也计入（工具 Schema 同样占用上下文窗口）。
        """
        messages = self.messages + list(extra_messages or [])
        if usage is None and extra_messages is None:
            # 两次请求之间优先沿用最近一次接口 usage；消息被修改时会重置。
            if self._last_usage_total:
                usage = {"total_tokens": self._last_usage_total}
        return self.context.snapshot(
            messages,
            usage=usage,
            tools=self.registry.openai_tools(),
            resolve_window=resolve_window,
        )

    def needs_compaction(self) -> bool:
        return self.context_snapshot(resolve_window=False).needs_compaction

    def _context_event(
        self,
        snapshot: ContextSnapshot,
        *,
        force_warning: bool = False,
    ) -> ContextUsage:
        warning = ""
        if snapshot.needs_compaction:
            if force_warning or not self._context_warned:
                warning = (
                    f"上下文使用量已达 {snapshot.percent:.1f}%"
                    f"（阈值 {snapshot.warn_ratio * 100:.0f}%），"
                    "已暂停普通提问。请先执行 /compact 压缩上下文。"
                )
                self._context_warned = True
        else:
            self._context_warned = False
        return ContextUsage(
            used_tokens=snapshot.used_tokens,
            window_tokens=snapshot.window_tokens,
            percent=snapshot.percent,
            window_is_default=snapshot.window_is_default,
            warning=warning,
        )

    def compact(self) -> Iterator[AgentEvent]:
        """手动压缩上下文：归档原文 → LLM 摘要 → 替换历史。

        压缩期间不调用任何工具，也不处理普通任务；只有摘要成功后才替换
        ``self.messages``。任何一步失败都保留原上下文，允许用户重试。
        """
        if self._compacting:
            yield CompactionFailed(message="上下文正在压缩中，请稍候")
            return

        self._compacting = True
        archive_id = ""
        try:
            # 至少要有 system 之外的实际对话才值得压缩。
            if len(self.messages) <= 1:
                yield CompactionFailed(message="当前没有可压缩的历史对话")
                return

            original_messages = [dict(message) for message in self.messages]
            original_text = format_messages(original_messages)

            # 1) 必须先完整落盘，保存失败则绝不继续压缩。
            record = self.context.archive.save(original_messages)
            archive_id = record.archive_id
            yield CompactionStarted(
                archive_id=record.archive_id,
                message_count=record.message_count,
                original_text=original_text,
            )

            # 2) 只调用一次不带工具的 LLM 生成摘要。
            summary = self.context.summarize(original_messages)

            # 3) 摘要成功后才替换上下文，保留 system prompt 和工具注册表。
            note = self.context.archive_note(record)
            system_message = (
                self.messages[0]
                if self.messages and self.messages[0].get("role") == "system"
                else {"role": "system", "content": self.system_prompt}
            )
            compacted = [system_message] + self.context.compacted_history(
                summary, note
            )
            # AGENTS.md 是长期项目约束，不参与摘要，压缩后继续复用。
            if self._agents_md_message is not None:
                compacted.append(dict(self._agents_md_message))
            self.messages = compacted
            self._last_usage_total = None
            self._context_warned = False

            snapshot = self.context_snapshot(resolve_window=False)
            yield CompactionFinished(
                archive_id=record.archive_id,
                archive_path=str(record.path),
                created_at=record.created_at,
                summary=summary,
                original_text=original_text,
                compacted_tokens=snapshot.used_tokens,
            )
            yield self._context_event(snapshot)
        except Exception as exc:
            # 归档成功但摘要失败时，原上下文仍然完整保留，归档文件也可保留。
            yield CompactionFailed(message=str(exc), archive_id=archive_id)
        finally:
            self._compacting = False

    def send(self, prompt: str) -> Iterator[AgentEvent]:
        """处理一次提问，返回事件流。

        界面可以边收事件边渲染（例如：刚进入一轮就显示“思考中”、每次工具调用单独列出、
        自动修正参数时提示用户、最终回答用打字机效果等）。
        """
        if self._compacting:
            error = "上下文正在压缩中，暂时不能处理普通问题"
            yield TurnFailed(message=error, kind="context_compacting")
            yield TurnFinished(result=TurnResult(prompt=prompt, error=error))
            return

        prompt = (prompt or "").strip()
        if not prompt:
            yield TurnFinished(result=TurnResult(prompt=prompt, error="问题不能为空"))
            return

        started_at = time.perf_counter()
        yield TurnStarted(prompt=prompt)

        # 达到阈值后暂停接受新的普通提问，直到 /compact 成功。
        current_snapshot = self.context_snapshot(resolve_window=False)
        yield self._context_event(
            current_snapshot, force_warning=current_snapshot.needs_compaction
        )
        if current_snapshot.needs_compaction:
            error = (
                f"上下文使用量已达 {current_snapshot.percent:.1f}%，"
                "已暂停普通提问。请先执行 /compact 压缩上下文。"
            )
            yield TurnFailed(message=error, kind="context_limit")
            yield TurnFinished(result=TurnResult(prompt=prompt, error=error))
            return

        turn_messages: list[dict] = [{"role": "user", "content": prompt}]

        # 把本次用户提示词也算进去再检查一次，避免大段输入直接冲过窗口。
        pending_snapshot = self.context_snapshot(
            extra_messages=turn_messages, resolve_window=False
        )
        if pending_snapshot.needs_compaction:
            yield self._context_event(pending_snapshot, force_warning=True)
            error = (
                f"加上本次提问后上下文将达到 {pending_snapshot.percent:.1f}%，"
                "已暂停普通提问。请先执行 /compact 压缩上下文。"
            )
            yield TurnFailed(message=error, kind="context_limit")
            yield TurnFinished(result=TurnResult(prompt=prompt, error=error))
            return

        nudged = False
        tool_runs: list[ToolRun] = []
        last_round_keys: set[tuple[str, str]] | None = None
        last_path = ""
        final_answer = ""

        try:
            for _ in range(self.config.max_tool_rounds):
                message = self.client.chat(
                    self.messages + turn_messages, tools=self.registry.openai_tools()
                )

                # 每次 LLM 响应后检查上下文占用；优先使用接口返回的 usage。
                usage = getattr(self.client, "last_usage", None)
                self._last_usage_total = usage_total_tokens(usage)
                snapshot = self.context_snapshot(
                    extra_messages=turn_messages
                    + [assistant_history_message(message)],
                    usage=usage,
                    resolve_window=True,
                )
                yield self._context_event(snapshot)

                calls = normalize_tool_calls(message, self.registry.names)

                if not calls:
                    hint = match_hint(prompt)
                    if hint and not nudged and not tool_runs:
                        nudged = True
                        spec = self.registry.get(hint)
                        nudge = build_nudge(hint, spec, prompt) if spec else ""
                        turn_messages.append(
                            assistant_history_message(message)
                        )
                        if nudge:
                            yield NudgeIssued(tool=hint, prompt=nudge)
                            turn_messages.append(
                                {"role": "user", "content": nudge}
                            )
                        else:
                            turn_messages.append(
                                {"role": "user", "content": match_hint("") or ""}
                            )
                        continue

                    final_answer = extract_content(message)
                    turn_messages.append(assistant_history_message(message))
                    break

                # 参数修正链
                for call in calls:
                    outcome = repair_arguments(
                        call.name, call.arguments, prompt, last_path
                    )
                    if outcome.arguments != dict(call.arguments):
                        call.arguments = outcome.arguments
                    for note in outcome.notes:
                        yield ArgumentRepaired(tool=call.name, detail=note)

                # 原地打转检测：连续两轮完全相同的工具调用
                round_keys = {
                    (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
                    for call in calls
                }
                if last_round_keys is not None and round_keys == last_round_keys:
                    if tool_runs:
                        name, result = tool_runs[-1].name, tool_runs[-1].result
                        final_answer = f"根据 {name} 工具的返回结果：\n{result}"
                    turn_messages.append(
                        {"role": "assistant", "content": final_answer}
                    )
                    break
                last_round_keys = round_keys

                turn_messages.append(assistant_history_message(message))

                # 同一轮里完全相同的调用只执行一次，结果共享
                result_cache: dict[tuple[str, str], str] = {}
                pairs: list[tuple[ToolCall, str]] = []
                for call in calls:
                    key = (
                        call.name,
                        json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
                    )
                    if key in result_cache:
                        pairs.append((call, result_cache[key]))
                        continue

                    yield ToolCallStarted(
                        call_id=call.call_id,
                        name=call.name,
                        arguments=call.arguments,
                        textual=call.textual,
                    )
                    started_tool = time.perf_counter()
                    tool_result = self.registry.execute(
                        call.name, call.arguments
                    )
                    duration_ms = int((time.perf_counter() - started_tool) * 1000)
                    content = tool_result.content

                    ok = tool_result.ok
                    yield ToolCallFinished(
                        call_id=call.call_id,
                        name=call.name,
                        arguments=call.arguments,
                        result=content,
                        ok=ok,
                        duration_ms=duration_ms,
                    )

                    if ok and call.name == "createSkill":
                        # 新 Skill 立即加入系统提示词中的 Skill 清单。
                        self.refresh_system_prompt()

                    if ok:
                        raw_path = call.arguments.get("path")
                        if isinstance(raw_path, str) and raw_path.strip():
                            try:
                                last_path = raw_path
                            except (ValueError, OSError):
                                pass

                    tool_runs.append(
                        ToolRun(
                            name=call.name,
                            arguments=dict(call.arguments),
                            result=content,
                            ok=ok,
                        )
                    )
                    result_cache[key] = content
                    pairs.append((call, content))
                    turn_messages.append(tool_result_message(call, content))

            else:
                # 循环正常结束（达到 MAX_TOOL_ROUNDS）
                if tool_runs:
                    name, result = tool_runs[-1].name, tool_runs[-1].result
                    final_answer = f"根据 {name} 工具的返回结果：\n{result}"

            if not final_answer and tool_runs:
                name, result = tool_runs[-1].name, tool_runs[-1].result
                final_answer = f"根据 {name} 工具的返回结果：\n{result}"

            if final_answer:
                yield AnswerReady(text=final_answer)
                # 把最终回答写入全局历史，保持多轮上下文
                self.messages.append({"role": "user", "content": prompt})
                self.messages.append({"role": "assistant", "content": final_answer})
            else:
                # 工具交互过程仍保留在上下文中，便于模型延续思路
                self.messages.extend(turn_messages)
        except Exception as exc:
            yield TurnFailed(message=str(exc), kind=getattr(exc, "kind", "error"))
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            yield TurnFinished(
                result=TurnResult(
                    prompt=prompt,
                    error=str(exc),
                    duration_ms=duration_ms,
                )
            )
            return

        # 一轮结束后再刷新一次，让 TUI 的状态条使用最新的历史消息。
        yield self._context_event(
            self.context_snapshot(resolve_window=False)
        )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        result = TurnResult(
            prompt=prompt,
            answer=final_answer,
            messages=tuple(turn_messages),
            tool_runs=tuple(tool_runs),
            duration_ms=duration_ms,
        )
        yield TurnFinished(result=result)
