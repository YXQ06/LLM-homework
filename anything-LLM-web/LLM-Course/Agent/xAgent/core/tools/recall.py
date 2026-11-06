"""recallContext：在历史上下文归档中做关键词检索。"""

from __future__ import annotations

import os
from typing import Any, Mapping

from ..context import ArchiveStore
from .base import ToolSpec, object_schema, text_argument


def _tool_recall_context(
    store: ArchiveStore,
    max_chars: int,
    max_results: int,
    arguments: Mapping[str, Any],
) -> str:
    text = text_argument(arguments, "text", required=True)
    archive_id = text_argument(arguments, "id")
    return store.search(
        text,
        archive_id=archive_id or None,
        max_chars=max_chars,
        max_results=max_results,
    )


def build_recall_tool(
    archive_dir: str | os.PathLike[str],
    *,
    max_chars: int = 6_000,
    max_results: int = 8,
) -> ToolSpec:
    """创建普通 Function Calling 工具 ``recallContext``。"""
    store = ArchiveStore(archive_dir)

    def handler(arguments: Mapping[str, Any]) -> str:
        return _tool_recall_context(
            store,
            max_chars=max_chars,
            max_results=max_results,
            arguments=arguments,
        )

    return ToolSpec(
        name="recallContext",
        description=(
            "检索上下文压缩时保存的历史对话归档。"
            "id 可选，指定归档编号（例如 20261022-153000）；"
            "text 必填，填写要查找的关键词或文本内容。"
            "当压缩摘要缺少历史细节时，用该工具找回原始记录。"
        ),
        parameters=object_schema(
            properties={
                "id": {
                    "type": "string",
                    "description": "可选，指定归档编号；不填则在所有历史归档中检索",
                },
                "text": {
                    "type": "string",
                    "description": "必填，需要查找的关键词或文本内容",
                },
            },
            required=["text"],
        ),
        handler=handler,
        example_arguments={
            "id": "20261022-153000",
            "text": "配置文件路径",
        },
    )


__all__ = ["build_recall_tool"]
