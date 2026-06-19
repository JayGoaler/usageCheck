"""AI Usage Check - 主入口"""

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import datetime as _dt
from datetime import datetime, timezone
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from collectors.base import CollectResult
from collectors.clash_collector import ClashCollector
from collectors.codex_collector import CodexQuotaCollector
from collectors.deepseek_collector import DeepSeekCollector
from collectors.deepseek_usage_collector import DeepSeekUsageCollector
from collectors.openai_collector import OpenAICollector
from services.data_store import DataStore
from services.scheduler import Scheduler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

config: dict[str, Any] = {}
data_store = DataStore()
scheduler = Scheduler()

# 单例采集器
codex_collector: CodexQuotaCollector | None = None
deepseek_usage_collector: DeepSeekUsageCollector | None = None

# SSE 全局事件
_last_event: str = ""
_collect_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# 采集逻辑
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    """解析 ${ENV_VAR} 为环境变量值"""
    def _replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")
    return re.sub(r'\$\{(\w+)\}', _replace, value)


async def run_collect() -> None:
    """执行一次完整的采集"""
    global config, codex_collector, deepseek_usage_collector
    async with _collect_lock:
        results: list[CollectResult] = []

        # Codex 订阅额度（App Server stdio）
        if codex_collector is not None:
            try:
                results.extend(await codex_collector.collect())
            except Exception:
                logger.exception("Codex collection failed")

        # OpenAI（组织 API 账单 — 保留但不用于仪表盘 Codex 卡片）
        openai_key = config.get("openai", {}).get("api_key", "")
        if openai_key:
            try:
                collector = OpenAICollector(openai_key)
                results.extend(await collector.collect())
            except Exception:
                pass

        # DeepSeek 余额
        deepseek_key = config.get("deepseek", {}).get("api_key", "")
        if deepseek_key:
            try:
                collector = DeepSeekCollector(deepseek_key)
                results.extend(await collector.collect())
            except Exception:
                pass

        # DeepSeek 月度用量（CDP 抓取，每日首次启动时自动抓取，后续走缓存）
        if deepseek_usage_collector is not None:
            try:
                results.extend(await deepseek_usage_collector.collect(force=False))
            except Exception:
                logger.exception("DeepSeek usage collection failed")

        # Clash / 妙妙屋代理平台
        proxy_cfg = config.get("proxy", config.get("clash", {}))
        proxy_email = proxy_cfg.get("email", "")
        proxy_password = proxy_cfg.get("password", "")
        proxy_url = proxy_cfg.get("site_url", "")
        if proxy_email and proxy_password and proxy_url:
            try:
                collector = ClashCollector(
                    site_url=proxy_url,
                    email=proxy_email,
                    password=proxy_password,
                )
                results.extend(await collector.collect())
            except Exception:
                pass

        if results:
            await data_store.save_results(results)
            _notify_clients(results)


def _notify_clients(results: list[CollectResult]) -> None:
    """更新全局 SSE 事件"""
    global _last_event
    message = json.dumps([
        {"source": r.source, "metric": r.metric, "value": r.value, "unit": r.unit, "detail": r.detail}
        for r in results
    ], default=str)
    _last_event = f"data: {message}\n\n"


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

async def load_config() -> None:
    """加载配置文件"""
    global config
    with open("config.yaml", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    # 解析环境变量
    for section in raw.values():
        if isinstance(section, dict):
            for k, v in section.items():
                if isinstance(v, str):
                    section[k] = _resolve_env(v)

    config = raw


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _safe_json(raw: str) -> dict[str, Any]:
    """安全解析 JSON 字符串，失败返回空字典"""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _fmt_ts(iso_str: str | None) -> str:
    """将 ISO 时间戳转换为本机时区并格式化。"""
    if not iso_str:
        return ""
    try:
        timestamp = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def _get_dashboard_response(latest: list[dict[str, Any]]) -> dict[str, Any]:
    """将数据库记录转换为前端期望的格式"""
    records: dict[str, dict[str, Any]] = {}
    for row in latest:
        key = f"{row['source']}:{row['metric']}"
        records[key] = row

    now = datetime.now(timezone.utc).astimezone()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    latest_timestamp = ""
    latest_moment: datetime | None = None
    for row in latest:
        raw_timestamp = row.get("timestamp")
        if not raw_timestamp:
            continue
        try:
            moment = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            moment = moment.astimezone()
        except (TypeError, ValueError):
            continue
        if latest_moment is None or moment > latest_moment:
            latest_moment = moment
            latest_timestamp = moment.strftime("%Y-%m-%d %H:%M:%S")

    # --- DeepSeek 余额 ---
    deepseek_record = records.get("deepseek:balance", {})
    deepseek_detail = _safe_json(deepseek_record.get("detail", "{}"))
    deepseek_balance = float(deepseek_record.get("value", 0))
    deepseek_balance_ts = _fmt_ts(deepseek_record.get("timestamp")) or now_str

    # --- DeepSeek 月度用量 ---
    usage_record = records.get("deepseek:monthly_tokens", {})
    usage_detail = _safe_json(usage_record.get("detail", "{}"))
    monthly_tokens = int(usage_record.get("value", 0))
    monthly_requests = usage_detail.get("requests", 0)
    monthly_cost = usage_detail.get("cost", 0)
    monthly_model = usage_detail.get("model", "deepseek-v4-pro")
    monthly_ts = _fmt_ts(usage_detail.get("updated") or usage_record.get("timestamp"))

    # --- Codex 订阅额度 ---
    codex_record = records.get("codex:rate_limit", {})
    codex_detail = _safe_json(codex_record.get("detail", "{}"))
    codex_available = bool(codex_detail)
    codex_plan = codex_detail.get("plan_type", "unknown")
    codex_primary = codex_detail.get("primary", {})
    codex_secondary = codex_detail.get("secondary", {})
    codex_ts = _fmt_ts(codex_record.get("timestamp")) or now_str
    # 转换 resetsAt Unix 时间戳为本地时间
    for window in (codex_primary, codex_secondary):
        ra = window.get("resets_at")
        if ra and ra > 0:
            try:
                window["reset_time"] = _dt.datetime.fromtimestamp(
                    ra, tz=timezone.utc
                ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                window["reset_time"] = str(ra)

    # --- VPN ---
    vpn_record = records.get("clash:traffic", {})
    vpn_detail = _safe_json(vpn_record.get("detail", "{}"))

    return {
        "lastUpdated": latest_timestamp,
        "deepseek": {
            "balance": deepseek_balance,
            "currency": deepseek_detail.get("currency", "CNY"),
            "lastUpdated": deepseek_balance_ts,
            "monthlyTokens": monthly_tokens,
            "monthlyRequests": monthly_requests,
            "monthlyCost": monthly_cost,
            "monthlyModel": monthly_model,
            "monthlyUpdated": monthly_ts,
        },
        "codex": {
            "available": codex_available,
            "planType": codex_plan,
            "primary": {
                "windowMinutes": codex_primary.get("window_minutes", 300),
                "usedPercent": codex_primary.get("used_percent", 0),
                "remainingPercent": codex_primary.get("remaining_percent", 100),
                "resetsAt": codex_primary.get("resets_at", 0),
                "resetTime": codex_primary.get("reset_time", "--"),
            },
            "secondary": {
                "windowMinutes": codex_secondary.get("window_minutes", 10080),
                "usedPercent": codex_secondary.get("used_percent", 0),
                "remainingPercent": codex_secondary.get("remaining_percent", 100),
                "resetsAt": codex_secondary.get("resets_at", 0),
                "resetTime": codex_secondary.get("reset_time", "--"),
            },
            "lastUpdated": codex_ts,
            "stale": not codex_available,
        },
        "vpn": {
            "totalBandwidth": float(vpn_detail.get("total_bandwidth", 500)),
            "usedBandwidth": float(vpn_detail.get("used_bandwidth", 0)),
            "remainingBandwidth": float(vpn_detail.get("remaining_bandwidth", 0)),
            "unit": "GB",
            "expiryDate": vpn_detail.get("expiry", "待确认"),
        },
    }


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------

static_dir = os.path.join(os.path.dirname(__file__), "web", "static")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭生命周期"""
    global config, codex_collector, deepseek_usage_collector

    await data_store.init()
    await load_config()

    # 初始化 Codex 采集器单例
    codex_cfg = config.get("codex", {})
    if codex_cfg.get("enabled", False):
        try:
            codex_collector = CodexQuotaCollector(config)
            await codex_collector._start()
        except Exception:
            logger.exception(
                "Failed to start Codex App Server; Codex card will show unavailable"
            )

    # 初始化 DeepSeek 月度用量采集器
    deepseek_usage_collector = DeepSeekUsageCollector(config, data_store)

    # 启动首次采集
    try:
        await run_collect()
    except Exception:
        logger.exception("Initial collection failed")

    interval = config.get("scheduler", {}).get("interval_minutes", 5)
    scheduler.start(run_collect, interval)

    yield

    scheduler.stop()
    if codex_collector:
        await codex_collector.close()
    await data_store.close()


app = FastAPI(lifespan=lifespan, title="AI Usage Check")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    """仪表盘页面 - 返回 SPA 入口"""
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/favicon.ico", response_class=FileResponse)
async def favicon() -> FileResponse:
    return FileResponse(os.path.join(static_dir, "favicon.ico"))


@app.get("/api/dashboard")
async def get_dashboard() -> dict[str, Any]:
    """获取仪表盘数据（匹配前端期望格式）"""
    latest = await data_store.get_latest()
    return _get_dashboard_response(latest)


@app.get("/api/status")
async def get_status() -> list[dict[str, Any]]:
    """获取当前所有数据源最新状态（原始格式）"""
    return await data_store.get_latest()


@app.get("/api/history/{source}")
async def get_history(source: str, limit: int = 50) -> list[dict[str, Any]]:
    """获取指定数据源历史记录"""
    return await data_store.get_history(source, limit)


@app.post("/api/config/interval")
async def update_interval(body: dict[str, Any]) -> dict[str, str]:
    """更新采集间隔"""
    minutes = int(body.get("minutes", 5))
    scheduler.change_interval(minutes)
    await data_store.set_config("interval", str(minutes))
    return {"status": "ok"}


@app.post("/api/collect/now")
async def collect_now() -> dict[str, str]:
    """手动触发一次采集"""
    await run_collect()
    return {"status": "ok"}


@app.post("/api/collect/usage")
async def collect_usage() -> dict[str, str]:
    """手动触发 DeepSeek 月度用量抓取（跳过当日缓存）"""
    global deepseek_usage_collector
    if deepseek_usage_collector is None:
        return JSONResponse(
            {"status": "error", "message": "DeepSeek usage collector not configured"},
            status_code=400,
        )
    try:
        results = await deepseek_usage_collector.collect(force=True)
        if results:
            await data_store.save_results(results)
            _notify_clients(results)
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500,
        )


@app.post("/api/collect/usage/login")
async def open_usage_login() -> dict[str, str]:
    """打开 Chrome 浏览器登录 DeepSeek 控制台"""
    try:
        DeepSeekUsageCollector.open_login_browser(config)
        return {
            "status": "ok",
            "message": "Chrome opened. Log in to DeepSeek, then click Refresh.",
        }
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500,
        )


# SSE 端点（占位）
@app.get("/api/events")
async def event_stream() -> JSONResponse:
    """SSE 实时推送（TODO: 完善实现）"""
    return JSONResponse({"status": "not implemented"})


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=config.get("server", {}).get("host", "127.0.0.1"),
        port=int(config.get("server", {}).get("port", 8080)),
        reload=True,
    )
