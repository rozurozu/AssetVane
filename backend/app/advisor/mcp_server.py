"""AssetVane 自前 Tool を codex に渡すための MCP サーバ（streamable HTTP・plans / ADR-005/014）。

設計の出所: docs/decisions.md ADR-012 の延長（codex 接続）/ plans。

codex は「外部が定義した function tool を注入して呼ばせる」口を持たない。自前 Tool を codex に
渡す正規ルートは MCP。本モジュールは FastAPI プロセス内に streamable HTTP の MCP エンドポイントを
立て、既存 `REGISTRY` の Tool（`openai_tools(phase)` と同集合）をそのまま公開する。

- 公開する Tool・スキーマ・handler は openai 経路と同一（REGISTRY が単一の真実）。挙動を揃える。
- handler は FastAPI プロセス内で動く＝**DB に触れるのは FastAPI だけ（ADR-005）**を保つ。
  codex（別プロセス）は HTTP 越しに呼ぶだけで DB を持たない。
- tool 呼び出しの「名前＋引数」は **codex app-server の item/completed イベント**から
  codex_engine が回収する（ADR-025＝結果値は載せない）。exec 時代の `X-AssetVane-Run` ヘッダ相関は
  app-server では不要（ターンを直列化し、イベントストリームを正とする＝plans 決定 4）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from app.advisor.tools.registry import CURRENT_PHASE, REGISTRY

logger = logging.getLogger(__name__)


def _build_server(phase: int) -> Server:
    """REGISTRY を MCP Tool として公開する low-level Server を組む（phase ゲート）。

    list_tools / call_tool とも openai 経路（registry.openai_tools・service.run_tool_loop）と
    同じ集合・同じ handler を使い、provider 差で挙動がぶれないようにする。
    """
    server: Server = Server("assetvane")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        # openai_tools(phase) と同じ min_phase ゲート。inputSchema は REGISTRY の parameters。
        return [
            mcp_types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.parameters,  # pydantic model_json_schema（openai と同一）
            )
            for t in REGISTRY.values()
            if t.min_phase <= phase
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        tool = REGISTRY.get(name)
        if tool is None:
            # openai 経路（service.run_tool_loop）と同じく落とさず error を返す。
            return {"error": "unknown tool"}
        # handler は dict を返す契約。low-level Server が structuredContent と JSON text に直す。
        return await tool.handler(arguments)

    return server


# 現在の session manager（lifespan が張り替える）。StreamableHTTPSessionManager.run() は
# 1 インスタンス 1 回限りのため、アプリ起動（lifespan）ごとに新しいインスタンスを作る。本番は
# 1 回だが、テストは TestClient を何度も起こす（lifespan 多発）ので singleton だと再入で落ちる。
_current_manager: StreamableHTTPSessionManager | None = None


async def _asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
    """MCP セッションマネージャへ素委譲する ASGI アプリ。

    lifespan 未起動（manager が無い）なら 503 を返す（codex 経路は lifespan 内でのみ使う）。
    """
    if scope.get("type") != "http":
        return
    manager = _current_manager
    if manager is None:
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"mcp not started"})
        return
    await manager.handle_request(scope, receive, send)  # type: ignore[arg-type]


def mount_mcp(app: object, path: str = "/mcp") -> None:
    """FastAPI に MCP の ASGI アプリをマウントする（main.lifespan で run() を回す前提）。

    path は codex の `mcp_servers.assetvane.url` と一致させる（settings.codex_mcp_url のパス部）。
    """
    app.mount(path, _asgi_app)  # type: ignore[attr-defined]


@asynccontextmanager
async def session_manager_lifespan() -> AsyncIterator[None]:
    """lifespan ごとに新しい session manager を作って run() を常駐させる（main.lifespan で使う）。

    StreamableHTTPSessionManager は内部タスクグループを run() の間だけ開き、1 インスタンス 1 回
    しか run() できない。アプリ起動の度に作り直し、終了時に外す。
    """
    global _current_manager
    # stateless＋json_response: codex app-server の MCP 呼び出しは 1 リクエスト 1 レスポンスで
    # 完結させる（SSE セッションを張らず軽い）。
    manager = StreamableHTTPSessionManager(
        app=_build_server(CURRENT_PHASE),
        json_response=True,
        stateless=True,
    )
    async with manager.run():
        _current_manager = manager
        try:
            yield
        finally:
            _current_manager = None
