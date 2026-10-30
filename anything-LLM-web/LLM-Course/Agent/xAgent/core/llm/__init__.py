"""大模型接口层（与具体界面无关）。"""

from .client import ChatClient, OpenAICompatibleClient

__all__ = ["ChatClient", "OpenAICompatibleClient"]
