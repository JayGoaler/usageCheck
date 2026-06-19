"""DeepSeek 月度用量采集 — Chrome CDP 抓取 platform.deepseek.com/usage"""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from typing import Any

import websocket  # websocket-client (sync)

from .base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)

DEFAULT_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class DeepSeekUsageCollector(BaseCollector):
    """通过 Chrome CDP 抓取 platform.deepseek.com/usage 获取月度 Token/请求/费用。"""

    def __init__(self, config: dict[str, Any], data_store=None) -> None:
        self._config = config
        self._data_store = data_store  # DataStore, used for daily cache check
        self._usage_cfg = config.get("deepseek_usage", {})

    # ── Chrome discovery ─────────────────────────────────────────────

    @staticmethod
    def _find_chrome(config: dict[str, Any]) -> str:
        """从 config 或默认路径查找 Chrome 可执行文件。"""
        usage_cfg = config.get("deepseek_usage", {})
        configured = usage_cfg.get("chrome_path", "")
        if configured and os.path.exists(configured):
            return configured
        for path in DEFAULT_CHROME_PATHS:
            if os.path.exists(path):
                return path
        raise RuntimeError(
            "Google Chrome not found. "
            "Set deepseek_usage.chrome_path in config.yaml"
        )

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _find_existing_debugger() -> dict[str, Any] | None:
        try:
            import requests
            targets = requests.get("http://127.0.0.1:9222/json", timeout=2).json()
            pages = [t for t in targets if t.get("type") == "page"]
            return pages[0] if pages else None
        except Exception:
            return None

    @staticmethod
    def _wait_for_debugger(port: int, timeout: float = 15) -> dict[str, Any]:
        import time as _time
        import requests
        url = f"http://127.0.0.1:{port}/json"
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            try:
                targets = requests.get(url, timeout=2).json()
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    return pages[0]
            except Exception:
                pass
            _time.sleep(0.5)
        raise RuntimeError(
            f"Chrome debugging endpoint did not start on port {port}"
        )

    # ── CDP interaction ──────────────────────────────────────────────

    @staticmethod
    def _scrape_page(page: dict[str, Any]) -> str:
        """通过 CDP 导航到用量页面并提取 body 文本。"""
        import time as _time
        ws_url = page["webSocketDebuggerUrl"]
        ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
        try:
            ws.send(json.dumps({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": "https://platform.deepseek.com/usage"},
            }))
            _time.sleep(7)

            ws.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "document.body.innerText",
                    "returnByValue": True,
                },
            }))

            deadline = _time.time() + 8
            while _time.time() < deadline:
                ws.settimeout(max(0.5, deadline - _time.time()))
                msg = json.loads(ws.recv())
                if msg.get("id") == 2:
                    return (
                        msg.get("result", {})
                        .get("result", {})
                        .get("value", "")
                    )
            return ""
        finally:
            ws.close()

    # ── parsing ──────────────────────────────────────────────────────

    def _parse_usage(self, text: str) -> dict[str, Any]:
        """从 DeepSeek 用量页面文本中提取模型统计。"""
        model = self._usage_cfg.get("model", "deepseek-v4-pro")

        if not text or model not in text:
            if "登录" in text or "注册" in text:
                raise RuntimeError(
                    "Login expired; call POST /api/collect/usage/login first"
                )
            raise RuntimeError(
                f"Model '{model}' not found on usage page; "
                "page structure may have changed"
            )

        chunk = text.split(model, 1)[1]
        for next_model in ("deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"):
            if next_model in chunk:
                chunk = chunk.split(next_model, 1)[0]

        # token count
        token_match = re.search(r"Tokens?\s*\n\s*([\d,]+)", chunk, re.IGNORECASE)
        tokens = int(token_match.group(1).replace(",", "")) if token_match else 0

        # request count
        req_match = re.search(
            r"(?:API[^\n]*|Requests?)\s*\n\s*([\d,]+)",
            chunk,
            re.IGNORECASE,
        )
        requests_count = int(req_match.group(1).replace(",", "")) if req_match else 0

        # cost — find CNY amounts; second occurrence is usually the per-model cost
        page_amounts = re.findall(
            r"([\d,]+(?:\.\d+)?)[ \t]*(?:CNY|RMB|元)",
            text,
            re.IGNORECASE,
        )
        if not page_amounts:
            page_amounts = re.findall(
                r"(?:CNY|RMB|¥|￥)[ \t]*([\d,]+(?:\.\d+)?)",
                text,
                re.IGNORECASE,
            )
        cost = 0.0
        if page_amounts:
            selected = page_amounts[1] if len(page_amounts) > 1 else page_amounts[0]
            cost = float(selected.replace(",", ""))

        if not tokens:
            raise RuntimeError("Could not parse monthly token usage from page")

        return {
            "model": model,
            "tokens": tokens,
            "requests": requests_count,
            "cost": cost,
        }

    # ── cache ─────────────────────────────────────────────────────────

    async def _is_today_cached(self) -> list[dict[str, Any]] | None:
        """检查今天是否已有月度用量记录。有则返回缓存行列表，无则返回 None。"""
        if self._data_store is None:
            return None
        today = datetime.now().astimezone().date()
        latest = await self._data_store.get_latest(source="deepseek")
        for row in latest:
            if row.get("metric") == "monthly_tokens":
                ts = row.get("timestamp", "")
                try:
                    timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    cached_date = timestamp.astimezone().date()
                except (TypeError, ValueError):
                    continue
                if cached_date == today:
                    logger.info("Today's monthly usage already cached")
                    return [row]
        return None

    # ── collect interface ────────────────────────────────────────────

    async def collect(self, force: bool = False) -> list[CollectResult]:
        """采集 DeepSeek 月度用量（非 force 时优先走当日缓存）。"""
        if not force:
            cached = await self._is_today_cached()
            if cached:
                row = cached[0]
                detail = row.get("detail", "{}")
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except (json.JSONDecodeError, TypeError):
                        detail = {}
                return [
                    CollectResult(
                        source="deepseek",
                        metric="monthly_tokens",
                        value=float(row.get("value", 0)),
                        unit="tokens",
                        detail=detail,
                    )
                ]

        chrome_path = self._find_chrome(self._config)
        profile = self._usage_cfg.get(
            "profile_path", r"C:\Temp\chrome_headless"
        )

        page = self._find_existing_debugger()
        chrome_proc = None

        if page is None:
            port = self._find_free_port()
            chrome_proc = subprocess.Popen(
                [
                    chrome_path,
                    "--headless=new",
                    f"--remote-debugging-port={port}",
                    "--remote-allow-origins=*",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--window-size=1920,1080",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                page = self._wait_for_debugger(port)
            except Exception:
                if chrome_proc and chrome_proc.poll() is None:
                    chrome_proc.terminate()
                    chrome_proc.wait(timeout=5)
                raise

        try:
            text = await asyncio.to_thread(self._scrape_page, page)
            parsed = self._parse_usage(text)
        finally:
            if chrome_proc is not None and chrome_proc.poll() is None:
                chrome_proc.terminate()
                try:
                    chrome_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome_proc.kill()

        now = datetime.now(timezone.utc).isoformat()
        detail = {
            "model": parsed["model"],
            "requests": parsed["requests"],
            "cost": parsed["cost"],
            "updated": now,
        }

        return [
            CollectResult(
                source="deepseek",
                metric="monthly_tokens",
                value=float(parsed["tokens"]),
                unit="tokens",
                detail=detail,
            )
        ]

    # ── login helper ─────────────────────────────────────────────────

    @staticmethod
    def open_login_browser(config: dict[str, Any]) -> None:
        """打开可见 Chrome 窗口用于手动登录 DeepSeek。"""
        usage_cfg = config.get("deepseek_usage", {})
        chrome_path = usage_cfg.get("chrome_path", "")
        if not chrome_path or not os.path.exists(chrome_path):
            for path in DEFAULT_CHROME_PATHS:
                if os.path.exists(path):
                    chrome_path = path
                    break
            else:
                raise RuntimeError("Chrome not found")

        profile = usage_cfg.get("profile_path", r"C:\Temp\chrome_headless")

        subprocess.Popen(
            [
                chrome_path,
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://platform.deepseek.com/usage",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
