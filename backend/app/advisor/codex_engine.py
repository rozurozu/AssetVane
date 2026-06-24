"""codex app-server 駆動エンジン（provider="codex"・plans / ADR-012/018）。

設計の出所: docs/decisions.md ADR-012 の延長（codex 接続）/ plans。backend-adapter-pattern 準拠
（外部プロセスもアダプタ越し・用途別の独自例外・リトライをこの層に隠す）。

codex は ChatGPT サブスク認証（API キー不要・限界費用ゼロ）で LLM を使える。本モジュールは
`codex app-server` を**常駐シングルトン**として 1 本だけ spawn し、stdio JSON-RPC（JSONL）で
喋る。自前 Tool は FastAPI 内 MCP（[[mcp_server]]）越しに呼ばせる。

なぜ app-server か（exec ではなく）:
- `codex exec` は非対話で MCP ツール呼び出しが常にキャンセルされる既知リグレッション
  （openai/codex #16685・#24135）。`--dangerously-bypass-approvals-and-sandbox` 無しでは
  MCP を呼べない。
- `codex app-server` は `mcpServer/elicitation/request` をプログラムで accept でき、危険フラグ
  不要・read-only サンドボックス維持で MCP が通る（実機検証済み・codex-cli 0.136.0）。

駆動の要点（実機 generate-json-schema＋スモークで確定した protocol）:
- 1 turn = 新規 thread（stateless・openai 経路と同じく毎回 CORE/POLICY＋全履歴を載せる）。
- CORE は thread/start の `baseInstructions`（ADR-015 の不変 base ペルソナ枠）、POLICY ほか
  system 群は `developerInstructions` に注入。会話は turn の text input に整形。
- ターンは async ロックで**直列化**（1 本の stdio パイプに全イベントが混ざるため）。
- tool_runs は `item/completed`（item.type=="mcpToolCall" & server=="assetvane"）から再構成
  （ADR-025＝result/error は載せない）。最終テキストは `item/completed` の agentMessage.text。
- 障害（ADR-018）: turn.status=="failed" の codexErrorInfo が一過性なら指数バックオフ再試行、
  恒久・タイムアウト・空応答は `CodexEngineError`。API への自動フォールバックはしない（plans）。
- usage: codex は USD コスト無し。token は thread/tokenUsage/updated から拾い cost_usd=0 で
  llm_usage に積む（Ollama と同じ・ADR-028）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile

from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


class CodexEngineError(RuntimeError):
    """codex app-server の失敗（turn 失敗・タイムアウト・空応答・プロセス断・再試行尽き）。

    呼び出し側（router は 502・nightly は journal スキップ＋Discord 通知）が翻訳する（ADR-018）。
    メッセージに codexErrorInfo / 末尾の手がかりを載せ、一過性判定に使う。
    """


# 一過性（再試行で回復しうる）失敗の手がかり。codexErrorInfo（camelCase enum）＋汎用語。
# 恒久（contextWindowExceeded / unauthorized / badRequest）は含めない＝再試行しない。
_TRANSIENT_MARKERS = (
    "serveroverloaded",
    "internalservererror",
    "usagelimitexceeded",
    "httpconnectionfailed",
    "responsestreamconnectionfailed",
    "overloaded",
    "rate limit",
    "rate_limit",
    "timed out",
    "temporarily",
    "-32001",
)


def _is_transient(text: str) -> bool:
    """失敗メッセージに一過性マーカーがあるか（再試行判定・ADR-018）。"""
    low = text.lower()
    return any(marker in low for marker in _TRANSIENT_MARKERS)


def _split_messages(messages: list[dict[str, object]]) -> tuple[str, str, str]:
    """build_messages の出力を (base_instructions, developer_instructions, prompt) に分ける。

    - base_instructions: 先頭 system（CORE）→ thread/start の baseInstructions（ADR-015 の
      不変 base ペルソナ枠に素直に対応）。
    - developer_instructions: 2 番目以降の system（POLICY・手法カード・文脈・画面）を連結。
    - prompt: user/assistant の会話を turn の text input に整形。1 ターンだけなら本文そのまま
      （夜/ドシエ）、複数ならラベル付きトランスクリプト（最後の user 発話が今回の依頼）。
    """
    system_parts: list[str] = []
    convo: list[dict[str, str]] = []
    for m in messages:
        role = str(m.get("role", ""))
        content = str(m.get("content", "") or "")
        if role == "system":
            if content:
                system_parts.append(content)
        else:
            convo.append({"role": role, "content": content})

    base = system_parts[0] if system_parts else ""
    developer = "\n\n".join(system_parts[1:])

    if len(convo) == 1 and convo[0]["role"] == "user":
        prompt = convo[0]["content"]
    else:
        labels = {"user": "ユーザー", "assistant": "アシスタント", "tool": "ツール結果"}
        lines = [f"{labels.get(m['role'], m['role'])}: {m['content']}" for m in convo]
        prompt = "これまでの会話（最後のユーザー発話が今回の依頼）:\n\n" + "\n\n".join(lines)
    return base, developer, prompt


class _AppServer:
    """`codex app-server` の常駐シングルトン JSON-RPC クライアント（plans 決定 1/2）。

    遅延 spawn・プロセス死亡時は次回呼び出しで再起動（スーパーバイズ）。ターンは `_turn_lock`
    で直列化し、stdio の単一パイプに混ざるイベントを 1 ターン分の Queue にだけ流す。
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._turn_lock = asyncio.Lock()  # ターン直列化（1 turn ずつ）
        self._write_lock = asyncio.Lock()  # stdin への書き込みを直列化
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._event_q: asyncio.Queue[tuple[str, dict[str, object]]] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    # -- プロセス管理 --------------------------------------------------------

    async def _ensure_started(self) -> None:
        """プロセスが生きていなければ spawn しハンドシェイクする（initialize→initialized）。"""
        if self._proc is not None and self._proc.returncode is None:
            return
        # 旧世代の reader/stderr を畳んでから作り直す（古い reader の EOF 処理が新世代の
        # _pending を消す世代競合を防ぐ。各タスクは捕捉した proc が現役のときだけ共有状態を触る）。
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        proc = await asyncio.create_subprocess_exec(
            settings.codex_bin,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        self._pending.clear()
        self._reader_task = asyncio.create_task(self._read_loop(proc))
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        try:
            await self._request(
                "initialize",
                {"clientInfo": {"name": "assetvane", "version": "0.1.0"}},
                timeout=settings.codex_startup_timeout_seconds,
            )
            await self._write({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        except Exception as exc:  # ハンドシェイク失敗はプロセスごと畳んで上げる
            await self._shutdown()
            raise CodexEngineError(f"codex app-server の初期化に失敗しました: {exc}") from exc

    async def _shutdown(self) -> None:
        """プロセスと reader を畳む（次回 _ensure_started で作り直す）。"""
        proc = self._proc
        self._proc = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CodexEngineError("codex app-server が停止しました"))
        self._pending.clear()
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            # kill 直後に returncode はセットされる。wait() は codex ラッパの子回収で詰まりうる
            # ので bound する（詰まっても reader が EOF で退き、次回 _ensure_started で作り直す）。
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError):
                pass

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """stderr を読み捨てる（パイプ詰まり防止）。中身は debug ログに残す。"""
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            logger.debug("codex app-server stderr: %s", line.decode("utf-8", "replace").rstrip())

    # -- JSON-RPC 入出力 -----------------------------------------------------

    async def _write(self, obj: dict[str, object]) -> None:
        """1 JSON オブジェクトを 1 行（JSONL）で stdin に書く。"""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise CodexEngineError("codex app-server が起動していません")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def _request(
        self, method: str, params: dict[str, object], *, timeout: float | None = None
    ) -> dict[str, object]:
        """id 付きリクエストを送り、対応するレスポンス result を待つ。"""
        self._next_id += 1
        rid = self._next_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[rid] = fut
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout or settings.codex_timeout_seconds)
        except TimeoutError as exc:
            raise CodexEngineError(f"codex app-server の {method} がタイムアウトしました") from exc
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        """stdout を 1 行ずつ読み、レスポンス／サーバ要求／通知に振り分ける。

        proc は spawn 時の世代。再 spawn 後は `self._proc is proc` が偽になるので、旧世代の
        reader は共有状態（_pending/_event_q/stdin）を触らず黙って退く。
        """
        if proc.stdout is None:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:  # EOF＝プロセス死亡
                if self._proc is proc:  # 現役世代だけが畳む（再 spawn 済みなら触らない）
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.set_exception(
                                CodexEngineError("codex app-server が異常終了しました")
                            )
                    self._pending.clear()
                    if self._event_q is not None:
                        self._event_q.put_nowait(("__eof__", {}))
                return
            if self._proc is not proc:  # 旧世代の残り行は無視
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            method = msg.get("method")
            if method is not None and "id" in msg:
                await self._handle_server_request(str(method), msg)
            elif method is not None:
                if self._event_q is not None:
                    self._event_q.put_nowait((str(method), msg))
            elif "id" in msg:
                fut = self._pending.get(int(msg["id"]))  # type: ignore[arg-type]
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(CodexEngineError(f"codex error: {msg['error']}"))
                    else:
                        fut.set_result(msg.get("result") or {})

    async def _handle_server_request(self, method: str, msg: dict[str, object]) -> None:
        """サーバ→クライアント要求に応答する。MCP elicitation は自前サーバ宛なら accept。"""
        rid = msg["id"]
        params = msg.get("params") or {}
        if method == "mcpServer/elicitation/request":
            server_name = params.get("serverName") if isinstance(params, dict) else None
            action = "accept" if server_name == "assetvane" else "decline"
            await self._write({"jsonrpc": "2.0", "id": rid, "result": {"action": action}})
            return
        # read-only＋approvalPolicy=never では承認系は来ない想定。来たら安全側に拒否（詰まり防止）。
        logger.warning("codex app-server から未対応のサーバ要求: %s（拒否）", method)
        await self._write(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unsupported"}}
        )

    # -- ターン実行 ----------------------------------------------------------

    async def run(
        self, *, base: str, developer: str, prompt: str, with_tools: bool, source: str, model: str
    ) -> tuple[str, list[dict[str, object]]]:
        """1 ターンを実行する（直列化＋一過性リトライ）。戻り値 (最終テキスト, tool_runs)。"""
        async with self._turn_lock:
            attempts = settings.codex_max_retries + 1
            last_exc: CodexEngineError | None = None
            for attempt in range(attempts):
                await self._ensure_started()
                try:
                    return await self._one_turn(
                        base=base,
                        developer=developer,
                        prompt=prompt,
                        with_tools=with_tools,
                        source=source,
                        model=model,
                    )
                except CodexEngineError as exc:
                    last_exc = exc
                    if attempt < attempts - 1 and _is_transient(str(exc)):
                        delay = settings.llm_retry_base_seconds * (2**attempt)
                        logger.warning(
                            "codex app-server が一過性失敗（%d/%d・%.1fs 後に再試行）: %s",
                            attempt + 1,
                            attempts,
                            delay,
                            exc,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
            assert last_exc is not None  # ループ構造上ここには来ない
            raise last_exc

    async def _one_turn(
        self, *, base: str, developer: str, prompt: str, with_tools: bool, source: str, model: str
    ) -> tuple[str, list[dict[str, object]]]:
        """thread/start → turn/start → turn/completed まで回し、テキストと tool_runs を返す。"""
        self._event_q = asyncio.Queue()
        # 面別に解決された model（空なら codex 既定にフォールバック＝ADR-058）。
        effective_model = model or settings.codex_model
        try:
            config: dict[str, object] = {}
            if with_tools:
                # 自前 Tool を FastAPI 内 MCP 越しに公開（DB は FastAPI に閉じる・ADR-005）。
                config["mcp_servers"] = {"assetvane": {"url": settings.codex_mcp_url}}
            if settings.codex_reasoning_effort:
                # 推論努力レベル（none/minimal/low/medium/high/xhigh）。空なら codex 既定。
                config["model_reasoning_effort"] = settings.codex_reasoning_effort
            thread_params: dict[str, object] = {
                "sandbox": settings.codex_sandbox,  # read-only（Advisor は書かない・ADR-005）
                "approvalPolicy": "never",  # 非対話（承認待ちで固まらない）
                "baseInstructions": base,  # CORE（ADR-015 の不変 base ペルソナ）
                "model": effective_model,
                "ephemeral": True,  # セッションを残さない
                "cwd": tempfile.gettempdir(),  # read-only ゆえ作業ディレクトリは scratch で十分
                "config": config,
            }
            if developer:
                thread_params["developerInstructions"] = developer  # POLICY ほか system 群
            start = await self._request("thread/start", thread_params)
            thread = start.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not thread_id:
                raise CodexEngineError("codex app-server が thread id を返しませんでした")

            await self._request(
                "turn/start",
                {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]},
            )
            return await self._drain_turn(str(thread_id), source=source, model=effective_model)
        finally:
            self._event_q = None

    async def _drain_turn(
        self, thread_id: str, *, source: str, model: str
    ) -> tuple[str, list[dict[str, object]]]:
        """turn/completed まで通知を読み、最終テキスト・tool_runs・usage を集約する。"""
        assert self._event_q is not None
        final_text = ""
        tool_runs: list[dict[str, object]] = []
        token_usage: dict[str, object] | None = None
        while True:
            try:
                method, msg = await asyncio.wait_for(
                    self._event_q.get(), timeout=settings.codex_timeout_seconds
                )
            except TimeoutError as exc:
                raise CodexEngineError(
                    f"codex app-server の turn がタイムアウトしました（{source}）"
                ) from exc

            params = msg.get("params") or {}
            if method == "__eof__":
                raise CodexEngineError(f"codex app-server がターン中に停止しました（{source}）")
            if method == "item/completed":
                item = params.get("item") if isinstance(params, dict) else None
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "agentMessage":
                    final_text = str(item.get("text") or "")  # 最後の agentMessage が最終回答
                elif itype == "mcpToolCall" and item.get("server") == "assetvane":
                    # ADR-025: name+args のみ（result/error は載せない）。
                    tool_runs.append(
                        {"name": item.get("tool"), "args": dict(item.get("arguments") or {})}
                    )
            elif method == "thread/tokenUsage/updated":
                tu = params.get("tokenUsage") if isinstance(params, dict) else None
                if isinstance(tu, dict):
                    token_usage = tu
            elif method == "turn/completed":
                turn = params.get("turn") if isinstance(params, dict) else None
                status = turn.get("status") if isinstance(turn, dict) else None
                if status == "failed":
                    err = (turn.get("error") or {}) if isinstance(turn, dict) else {}
                    info = err.get("codexErrorInfo")
                    raise CodexEngineError(
                        f"codex turn 失敗（{source}・{info}）: {err.get('message')}"
                    )
                break
            elif method == "error":
                raise CodexEngineError(f"codex app-server エラー（{source}）: {params}")

        _record_usage(token_usage, source=source, model=model)
        if not final_text:
            raise CodexEngineError(f"codex app-server が空応答を返しました（{source}）")
        return final_text, tool_runs


def _record_usage(token_usage: dict[str, object] | None, *, source: str, model: str) -> None:
    """tokenUsage の total から token を llm_usage に積む（cost_usd=0・ADR-028/058）。

    model は面別に解決された codex モデル（監査用）。ChatGPT サブスク経由のため cost_usd は常に 0。
    """
    tokens_in: int | None = None
    tokens_out: int | None = None
    if isinstance(token_usage, dict):
        total = token_usage.get("total")
        if isinstance(total, dict):
            tokens_in = total.get("inputTokens")  # type: ignore[assignment]
            tokens_out = total.get("outputTokens")  # type: ignore[assignment]
    try:
        with get_engine().begin() as conn:
            repo.insert_llm_usage(
                conn,
                source=source,
                model=model or settings.codex_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=0.0,  # ChatGPT サブスク経由は per-call の USD コストが無い
            )
    except Exception:
        logger.exception("codex の llm_usage 計上に失敗（応答は返す）")


# プロセス内で 1 本だけ使い回す常駐シングルトン（plans 決定 1）。
_server = _AppServer()


async def shutdown() -> None:
    """常駐 app-server を畳む（FastAPI 終了時に呼び、codex 子プロセスの孤児化を防ぐ）。"""
    await _server._shutdown()


async def run_turn(
    messages: list[dict[str, object]],
    *,
    phase: int,
    source: str,
    model: str = "",
) -> tuple[str, list[dict[str, object]]]:
    """エージェント形（Tool ループ）を codex app-server で回す。service.run_tool_loop の代替。

    （plans・ADR-014/018/025/058）

    codex が MCP（FastAPI 内・[[mcp_server]]）越しに自前 Tool を呼ぶ。呼ばれた Tool の名前＋
    引数は app-server の item/completed イベントから回収する（結果値は載せない＝ADR-025）。
    phase はインターフェース整合のため受けるが、露出 Tool 集合は MCP サーバ側が CURRENT_PHASE で
    固定している（openai_tools と同集合）。model は面別に解決された codex モデル（空なら
    settings.codex_model フォールバック＝ADR-058）。

    戻り値: (最終テキスト, tool_runs)。tool_runs は [{name, args}]。
    """
    base, developer, prompt = _split_messages(messages)
    return await _server.run(
        base=base, developer=developer, prompt=prompt, with_tools=True, source=source, model=model
    )


async def generate_once(messages: list[dict[str, object]], *, source: str, model: str = "") -> str:
    """単発テキスト形（Tool 無し）を codex app-server で生成する。llm.complete の代替。

    （plans・ADR-014/058）

    ドシエ要約（dossier.summarize_dossier）等、事前計算した事実を渡して文章/JSON を返すだけの
    用途。MCP は付けない（with_tools=False）。最終テキストをそのまま返す（呼び出し側が必要なら
    JSON パースする）。model は面別に解決された codex モデル（空なら settings.codex_model）。
    """
    base, developer, prompt = _split_messages(messages)
    text, _ = await _server.run(
        base=base, developer=developer, prompt=prompt, with_tools=False, source=source, model=model
    )
    return text
