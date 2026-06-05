"""codex 接続用 MCP サーバ（REGISTRY 公開）を検証する（plans・ADR-005/014/025）。

担保すること:
- list_tools が openai_tools(CURRENT_PHASE) と同集合（provider 差で露出 Tool がぶれない）。
- call_tool が REGISTRY の handler を呼び dict を返す（未知 Tool は落とさず error）。
  tool 呼び出しの「名前＋引数」は codex app-server の item/completed イベントから codex_engine が
  回収する（plans 決定 4）。MCP サーバ側にヘッダ相関の記録は持たない。

DB に触れる handler は一時 SQLite（temp_db）。ネットには出ない＝testing-strategy。
"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as mcp_types

from app.advisor import mcp_server
from app.advisor.tools.registry import CURRENT_PHASE, openai_tools


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_list_tools_matches_openai_tools() -> None:
    """MCP が公開する Tool 集合は openai_tools(CURRENT_PHASE) と一致する。"""
    server = mcp_server._build_server(CURRENT_PHASE)
    res = _run(
        server.request_handlers[mcp_types.ListToolsRequest](
            mcp_types.ListToolsRequest(method="tools/list")
        )
    )
    names = sorted(tool.name for tool in res.root.tools)
    expected = sorted(f["function"]["name"] for f in openai_tools(CURRENT_PHASE))
    assert names == expected


def test_call_tool_dispatches_to_registry_handler(temp_db: None) -> None:
    """call_tool は REGISTRY の handler を実行し dict を返す（記録は持たない）。"""
    server = mcp_server._build_server(CURRENT_PHASE)
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name="get_asset_overview", arguments={}),
    )
    result = _run(server.request_handlers[mcp_types.CallToolRequest](req))
    # handler が呼ばれて結果（dict）が返っている（中身の値は問わない）。
    assert result.root.isError is False


def test_unknown_tool_returns_error_not_crash() -> None:
    """未知 Tool 名でも落とさず {"error": "unknown tool"} を返す（openai 経路と同じ寛容さ）。"""
    server = mcp_server._build_server(CURRENT_PHASE)
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name="no_such_tool", arguments={}),
    )
    result = _run(server.request_handlers[mcp_types.CallToolRequest](req))
    # 例外ではなく error dict（service.run_tool_loop の未知 Tool 扱いと同じ・落とさない）。
    assert result.root.isError is False
    assert result.root.structuredContent == {"error": "unknown tool"}
