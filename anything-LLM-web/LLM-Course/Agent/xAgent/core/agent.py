"""Agent 核心编排：多轮对话、工具调用、参数容错、漏调兜底、循环检测。

本模块是“界面无关”的业务逻辑，所有对外交互都通过 ``AgentEvent`` 流传递，
TUI、Web、Desktop 都只需要消费这些事件即可。
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from .config import AgentConfig
from .events import (
    AgentEvent,
    AnswerReady,
    ArgumentRepaired,
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
from .tools.base import ToolRegistry


class Agent:
    """多轮对话 Agent：工具循环、参数容错、漏调追问、原地打转检测。"""

    def __init__(
        self,
        client: OpenAICompatibleClient,
        registry: ToolRegistry,
        config: AgentConfig,
        system_prompt: str | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.config = config
        self.system_prompt = system_prompt or build_system_prompt(registry)
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

    def reset(self) -> None:
        """清空对话历史，仅保留系统提示词。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def send(self, prompt: str) -> Iterator[AgentEvent]:
        """处理一次提问，返回事件流。

        界面可以边收事件边渲染（例如：刚进入一轮就显示“思考中”、每次工具调用单独列出、
        自动修正参数时提示用户、最终回答用打字机效果等）。
        """
        prompt = (prompt or "").strip()
        if not prompt:
            yield TurnFinished(result=TurnResult(prompt=prompt, error="问题不能为空"))
            return

        started_at = time.perf_counter()
        yield TurnStarted(prompt=prompt)

        turn_messages: list[dict] = [{"role": "user", "content": prompt}]
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

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        result = TurnResult(
            prompt=prompt,
            answer=final_answer,
            messages=tuple(turn_messages),
            tool_runs=tuple(tool_runs),
            duration_ms=duration_ms,
        )
        yield TurnFinished(result=result)
