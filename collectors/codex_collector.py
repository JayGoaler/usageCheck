"""Codex App Server 订阅额度采集 — JSON-RPC over stdio"""
import asyncio
import concurrent.futures
import json
import logging
import shutil
import subprocess
import threading
from typing import Any

from .base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)


class CodexQuotaCollector(BaseCollector):
    """通过 codex app-server --stdio 查询 ChatGPT Codex 订阅额度。

    使用 subprocess.Popen + 线程方案，兼容 Windows asyncio 限制。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._proc: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._initialized = False
        self._account_info: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    # ── command resolution ──────────────────────────────────────────

    def _resolve_command(self) -> list[str]:
        """从 config 或 PATH 解析 codex 命令。"""
        cmd = self._config.get("codex", {}).get("command")
        if cmd and isinstance(cmd, list) and len(cmd) > 0:
            logger.info("Using configured codex command: %s", cmd)
            return list(cmd)

        for name in ("codex.cmd", "codex"):
            path = shutil.which(name)
            if path:
                logger.info("Found codex at: %s", path)
                return [path, "app-server", "--stdio"]

        raise RuntimeError(
            "Codex CLI not found. Install Codex or set codex.command in config.yaml"
        )

    # ── RPC state management ────────────────────────────────────────

    def _reset_rpc_state(self, reason: str) -> None:
        """清理 pending futures 并重置请求序列，用于 App Server 重启/关闭。"""
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
            self._request_id = 0

        for future in pending:
            if future.done():
                continue
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(
                    future.set_exception,
                    RuntimeError(reason),
                )
            else:
                future.set_exception(RuntimeError(reason))

    # ── subprocess lifecycle ────────────────────────────────────────

    async def _start(self) -> None:
        """启动 codex app-server --stdio 子进程并完成初始化。"""
        if self._proc is not None and self._proc.poll() is None:
            return

        self._reset_rpc_state("App Server restarted")

        cmd = self._resolve_command()
        logger.info("Starting Codex App Server: %s", cmd)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        self._loop = asyncio.get_running_loop()
        self._running = True

        # Start reader thread
        self._reader_thread = threading.Thread(
            target=self._read_stdout_sync,
            daemon=True,
        )
        self._reader_thread.start()

        # initialize RPC (use monotonic id, not fixed 0, to avoid reconnect conflicts)
        await self._rpc_call(
            "initialize",
            params={
                "clientInfo": {
                    "name": "usage_check",
                    "title": "AI Usage Check",
                    "version": "1.0.0",
                }
            },
        )

        # initialized notification
        self._write_line(json.dumps({"method": "initialized", "params": {}}))

        # account check
        account = await self._rpc_call("account/read", params={"refreshToken": False})
        acc = account.get("account", {})
        if acc.get("type") != "chatgpt":
            raise RuntimeError(
                f"Expected chatgpt account, got: {acc.get('type')}. "
                "Please log in to Codex Desktop with the same Windows user."
            )
        self._account_info = {
            "type": acc.get("type", "unknown"),
            "plan_type": acc.get("planType", "unknown"),
        }
        self._initialized = True
        logger.info("Codex App Server initialized: %s", self._account_info)

    # ── stdin ───────────────────────────────────────────────────────

    def _write_line(self, line: str) -> None:
        """向子进程 stdin 写入一行（线程安全）。"""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("App Server not started")
        with self._write_lock:
            self._proc.stdin.write((line + "\n").encode())
            self._proc.stdin.flush()

    # ── stdout reader (sync thread) ─────────────────────────────────

    def _read_stdout_sync(self) -> None:
        """在独立线程中持续读取 stdout，按 id 匹配 resolve pending futures。"""
        assert self._proc is not None and self._proc.stdout is not None
        assert self._loop is not None
        loop = self._loop
        try:
            while self._running:
                line = self._proc.stdout.readline()
                if not line:
                    break  # EOF
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if msg_id is not None:
                    with self._pending_lock:
                        future = self._pending.pop(msg_id, None)
                    if future is not None and not future.done():
                        if "error" in msg:
                            err_msg = msg["error"].get("message", str(msg["error"]))
                            loop.call_soon_threadsafe(
                                future.set_exception, RuntimeError(err_msg)
                            )
                        else:
                            loop.call_soon_threadsafe(
                                future.set_result, msg.get("result", {})
                            )
        except Exception:
            if self._running:
                logger.exception("stdout reader thread error")

    # ── JSON-RPC ────────────────────────────────────────────────────

    async def _rpc_call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        rpc_id: int | None = None,
    ) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待匹配的响应。"""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("App Server not started")

        if rpc_id is None:
            self._request_id += 1
            rpc_id = self._request_id

        timeout = self._config.get("codex", {}).get("request_timeout_seconds", 15)
        assert self._loop is not None
        future: asyncio.Future[dict[str, Any]] = self._loop.create_future()

        with self._pending_lock:
            self._pending[rpc_id] = future

        request: dict[str, Any] = {"method": method, "id": rpc_id}
        if params:
            request["params"] = params

        # Write via thread to avoid blocking the event loop
        await asyncio.to_thread(self._write_line, json.dumps(request))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._pending_lock:
                self._pending.pop(rpc_id, None)
            raise RuntimeError(f"RPC call '{method}' timed out after {timeout}s")

    # ── collect interface ───────────────────────────────────────────

    async def collect(self) -> list[CollectResult]:
        """查询 Codex 订阅额度。首次超时重试一次。"""
        if not self._initialized:
            await self._start()

        codex_config = self._config.get("codex", {})
        retry_count = max(0, int(codex_config.get("initial_retry_count", 1)))
        retry_delay = max(
            0.0,
            float(codex_config.get("initial_retry_delay_seconds", 1.0)),
        )
        attempt = 0
        result: dict[str, Any] = {}

        while True:
            try:
                result = await self._rpc_call("account/rateLimits/read")
                break
            except RuntimeError as error:
                is_timeout = (
                    "account/rateLimits/read" in str(error)
                    and "timed out" in str(error)
                )
                if not is_timeout or attempt >= retry_count:
                    raise
                attempt += 1
                logger.warning(
                    "Codex quota request timed out; retrying in %.1f seconds "
                    "(attempt %s/%s)",
                    retry_delay,
                    attempt,
                    retry_count,
                )
                await asyncio.sleep(retry_delay)

        limits_by_id = result.get("rateLimitsByLimitId", {})
        limits_list = result.get("rateLimits", [])

        if limits_by_id:
            for limit_id, bucket in limits_by_id.items():
                return self._build_result(limit_id, bucket)
        elif limits_list:
            bucket = limits_list[0] if isinstance(limits_list, list) else limits_list
            return self._build_result(bucket.get("limitId", "codex"), bucket)

        return []

    def _build_result(
        self, limit_id: str, bucket: dict[str, Any]
    ) -> list[CollectResult]:
        """将额度桶标准化为 CollectResult。"""

        def _safe_pct(pct: float) -> int:
            return max(0, min(100, round(100 - pct)))

        primary = bucket.get("primary", {})
        secondary = bucket.get("secondary", {})

        primary_remaining = _safe_pct(primary.get("usedPercent", 0))

        detail = {
            "account_type": self._account_info.get("type", "unknown"),
            "plan_type": self._account_info.get("plan_type", "unknown"),
            "limit_id": limit_id,
            "primary": {
                "used_percent": primary.get("usedPercent", 0),
                "remaining_percent": primary_remaining,
                "window_minutes": primary.get("windowDurationMins", 0),
                "resets_at": primary.get("resetsAt", 0),
            },
            "secondary": {
                "used_percent": secondary.get("usedPercent", 0),
                "remaining_percent": _safe_pct(secondary.get("usedPercent", 0)),
                "window_minutes": secondary.get("windowDurationMins", 0),
                "resets_at": secondary.get("resetsAt", 0),
            },
            "collector": "app_server",
        }

        return [
            CollectResult(
                source="codex",
                metric="rate_limit",
                value=float(primary_remaining),
                unit="%",
                detail=detail,
            )
        ]

    # ── cleanup ─────────────────────────────────────────────────────

    async def close(self) -> None:
        """关闭 App Server 子进程。"""
        self._running = False
        self._reset_rpc_state("App Server closed")

        if self._proc:
            # Close stdin to signal EOF
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass

            # Join reader thread
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=3)

            # Terminate process
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            except Exception:
                pass
            self._proc = None

        self._initialized = False
        logger.info("Codex App Server closed")
