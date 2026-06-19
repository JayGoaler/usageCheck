"""OpenAI Codex 额度查询"""
from collections.abc import MutableMapping, Sequence
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import BaseCollector, CollectResult


class OpenAICollector(BaseCollector):
    """通过 OpenAI API 查询 Codex 额度"""

    BASE_URL = "https://api.openai.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def collect(self) -> list[CollectResult]:
        """查询 OpenAI 账户用量"""
        now = datetime.now(timezone.utc)

        async with httpx.AsyncClient(base_url=self.BASE_URL, headers=self._headers, timeout=30) as client:
            # 成本查询 — GET /organization/costs
            start_date = now.strftime("%Y-%m-%d")
            end_date = now.strftime("%Y-%m-%d")
            costs_resp = await client.get(
                "/organization/costs",
                params={"start_time": start_date, "end_time": end_date, "bucket_width": "1d"},
            )
            costs_resp.raise_for_status()
            costs_data: MutableMapping[str, Any] = costs_resp.json()

            # 提取当日总消费
            total_cost = 0.0
            for bucket in costs_data.get("data", []):
                for result in bucket.get("results", []):
                    total_cost += float(result.get("cost", {}).get("amount", 0))

            # 查询总使用量 — 获取本月数据
            month_start = now.strftime("%Y-%m-01")
            usage_resp = await client.get(
                "/organization/usage/completions",
                params={"start_time": month_start, "end_time": end_date},
            )
            usage_data: MutableMapping[str, Any] = {}
            if usage_resp.status_code == 200:
                usage_data = usage_resp.json()

            # 统计本月总 token 使用量
            total_tokens = 0
            for bucket in usage_data.get("data", []):
                for result in bucket.get("results", []):
                    total_tokens += result.get("input_tokens", 0) + result.get("output_tokens", 0)

            # 获取订阅/配额信息 — 尝试 billing endpoint
            # OpenAI 的配额信息通常在订阅页面，非 API 直接返回，
            # 这里通过 dashboard 的 subscription 端点获取
            credit_total = 0.0
            credit_used = total_cost
            try:
                sub_resp = await client.get("/v1/dashboard/billing/subscription")
                if sub_resp.status_code == 200:
                    sub_data = sub_resp.json()
                    credit_total = float(sub_data.get("hard_limit_usd", 0)) or float(
                        sub_data.get("soft_limit_usd", 0)
                    )
            except Exception:
                pass

            return [
                CollectResult(
                    source="openai",
                    metric="credit",
                    value=total_cost,
                    unit="USD",
                    detail={
                        "used": total_cost,
                        "total": credit_total,
                        "remaining": max(0, credit_total - total_cost),
                        "monthly_tokens": total_tokens,
                        "daily_cost": total_cost,
                    },
                ),
            ]