"""耕云 xAgent 本地网页服务。

用途
----
1. 托管项目根目录的 ``index.html``（AnythingLLM 简易前端）；
2. 把浏览器对 ``/anythingllm/*`` 的请求反向代理到 AnythingLLM 的
   ``/api/v1/*``，并在服务端从本地 ``.env`` 读取 API Key 后注入
   ``Authorization: Bearer ...``。

这样浏览器端既不需要保存密钥，也不会遇到跨域（CORS）问题。
仅使用 Python 标准库，不需要安装 Flask 等额外依赖。

用法
----
``python serve_web.py``                 启动服务并打印访问地址
``python serve_web.py --port 8080``     指定端口
``python serve_web.py --check``         只检查 .env 配置（密钥脱敏）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
API_PREFIX = "/anythingllm"
UPSTREAM_API_PATH = "/api/v1"


# ---------------------------------------------------------------------------
# .env 读取与配置
# ---------------------------------------------------------------------------


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """读取简单的 KEY=VALUE 文件；同名环境变量优先级更高。"""
    values: dict[str, str] = {}
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            raw = ""
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 允许值两侧带引号，也允许行尾 # 注释（简单场景）。
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key:
                values[key] = value
    for key in (
        "ANYTHINGLLM_BASE_URL",
        "ANYTHINGLLM_API_KEY",
        "ANYTHINGLLM_WORKSPACE",
        "XAGENT_WEB_HOST",
        "XAGENT_WEB_PORT",
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def web_settings() -> dict[str, str]:
    env = load_env()
    return {
        "base_url": (env.get("ANYTHINGLLM_BASE_URL") or "http://localhost:3001").rstrip("/"),
        "api_key": env.get("ANYTHINGLLM_API_KEY") or "",
        "workspace": env.get("ANYTHINGLLM_WORKSPACE") or "",
        "host": env.get("XAGENT_WEB_HOST") or "127.0.0.1",
        "port": env.get("XAGENT_WEB_PORT") or "8080",
    }


def mask_secret(value: str) -> str:
    if not value:
        return "（未配置）"
    if len(value) <= 8:
        return value[:2] + "*" * (len(value) - 2)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------


class Handler(SimpleHTTPRequestHandler):
    """静态文件 + AnythingLLM 反向代理。"""

    server_version = "GengYunWeb/1.0"

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    # ------------------------------------------------------------- 基础响应
    def _send_json(
        self, payload: dict | list, status: int = 200, extra_headers: dict | None = None
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def end_headers(self) -> None:
        # 静态资源也允许被本地调试工具访问；不携带 Cookie。
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stdout.write("[web] " + (fmt % args) + "\n")

    # ------------------------------------------------------------- 路由
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization, Accept"
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == f"{API_PREFIX}/config":
            self._handle_config()
            return
        if parts.path.startswith(API_PREFIX + "/"):
            self._proxy("GET")
            return
        if parts.path in ("/", "/index.html"):
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path.startswith(API_PREFIX + "/"):
            self._proxy("POST")
            return
        self._send_error_json(404, "未找到该接口")

    # ------------------------------------------------------------- 配置接口
    def _handle_config(self) -> None:
        settings = web_settings()
        self._send_json(
            {
                "baseUrl": API_PREFIX,
                "workspace": settings["workspace"],
                "hasApiKey": bool(settings["api_key"]),
                "upstream": settings["base_url"],
            }
        )

    # ------------------------------------------------------------- 反向代理
    def _proxy(self, method: str) -> None:
        settings = web_settings()
        if not settings["api_key"]:
            self._send_error_json(
                500,
                "未配置 ANYTHINGLLM_API_KEY：请在 xAgent/.env 中填写后重启 serve_web.py",
            )
            return

        parts = urlsplit(self.path)
        rest = parts.path[len(API_PREFIX) :].lstrip("/")
        if not rest:
            self._send_error_json(404, "缺少 AnythingLLM 接口路径")
            return
        # 允许调用方直接写 api/v1/...，其余路径自动补上 /api/v1。
        upstream_path = (
            "/" + rest if rest.startswith("api/") else f"{UPSTREAM_API_PATH}/{rest}"
        )
        url = settings["base_url"] + upstream_path
        if parts.query:
            url += "?" + parts.query

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Authorization", f"Bearer {settings['api_key']}")
        request.add_header("Accept", "application/json")
        content_type = self.headers.get("Content-Type")
        if content_type:
            request.add_header("Content-Type", content_type)

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
                status = response.status
                response_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            response_type = exc.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._send_error_json(
                502,
                f"无法连接 AnythingLLM（{settings['base_url']}）：{exc}。"
                "请确认 AnythingLLM 已启动，且 ANYTHINGLLM_BASE_URL 配置正确。",
            )
            return

        self.send_response(status)
        self.send_header("Content-Type", response_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def run(host: str, port: int) -> None:
    handler = partial(Handler, directory=str(PROJECT_DIR))
    server = ThreadingHTTPServer((host, port), handler)
    settings = web_settings()
    print("耕云 · xAgent 本地网页服务已启动")
    print(f"  访问地址：http://{host}:{port}/")
    print(f"  AnythingLLM：{settings['base_url']}")
    print(f"  API Key：{mask_secret(settings['api_key'])}")
    print(f"  默认工作区：{settings['workspace'] or '（前端自动选择）'}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    settings = web_settings()
    parser = argparse.ArgumentParser(description="耕云 xAgent 本地网页服务")
    parser.add_argument("--host", default=settings["host"])
    parser.add_argument("--port", type=int, default=int(settings["port"]))
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查 .env 配置并退出（密钥脱敏显示）",
    )
    args = parser.parse_args(argv)

    if args.check:
        print(f"配置文件：{ENV_FILE}（{'存在' if ENV_FILE.is_file() else '不存在'}）")
        print(f"ANYTHINGLLM_BASE_URL = {settings['base_url']}")
        print(f"ANYTHINGLLM_API_KEY  = {mask_secret(settings['api_key'])}")
        print(f"ANYTHINGLLM_WORKSPACE= {settings['workspace'] or '（未设置）'}")
        if not settings["api_key"]:
            print("提示：API Key 为空，前端将无法调用 AnythingLLM。")
        return

    try:
        run(args.host, args.port)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
