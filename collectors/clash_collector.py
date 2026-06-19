"""妙妙屋代理平台流量采集器

通过 HTTP 登录代理平台网站，从 /user 页面抓取流量使用数据。
数据来源于服务端渲染的 HTML 中内嵌的 trafficDountChat() 调用参数。
"""

import re
from typing import Any

import httpx

from .base import BaseCollector, CollectResult


class ClashCollector(BaseCollector):
    """通过 HTTP 登录 + 页面抓取获取代理流量信息"""

    def __init__(
        self,
        site_url: str,
        email: str = "",
        password: str = "",
    ) -> None:
        """
        Args:
            site_url: 代理平台站点 URL（从配置/环境变量获取）
            email: 登录邮箱
            password: 登录密码
        """
        self._site_url = site_url.rstrip("/")
        self._email = email
        self._password = password

    async def collect(self) -> list[CollectResult]:
        """登录网站并抓取流量数据"""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # 1. 登录获取认证 Cookie
            login_resp = await client.post(
                f"{self._site_url}/auth/login",
                data={
                    "email": self._email,
                    "passwd": self._password,
                    "code": "",
                    "remember_me": None,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            login_resp.raise_for_status()

            login_data = login_resp.json()
            if login_data.get("ret") != 1:
                raise RuntimeError(
                    f"登录失败: {login_data.get('msg', '未知错误')}"
                )

            # 2. 请求 /user 页面获取流量数据
            user_resp = await client.get(f"{self._site_url}/user")
            user_resp.raise_for_status()
            html = user_resp.text

            # 3. 从 HTML 中解析 trafficDountChat() 调用参数
            # 格式: trafficDountChat('33.37GB', '48.94MB', '64.82GB', '33.97', '0.05', '65.98')
            traffic_data = self._parse_traffic_data(html)
            if traffic_data is None:
                raise RuntimeError("无法从页面中解析流量数据")

            used_total_str = traffic_data["used_total_str"]      # e.g. "33.37GB"
            today_used_str = traffic_data["today_used_str"]      # e.g. "48.94MB"
            remaining_str = traffic_data["remaining_str"]         # e.g. "64.82GB"
            used_percent = traffic_data["used_percent"]           # 33.97
            today_percent = traffic_data["today_percent"]         # 0.05
            remaining_percent = traffic_data["remaining_percent"] # 65.98

            # 4. 解析为字节数
            used_total_bytes = self._parse_storage_to_bytes(used_total_str)
            today_used_bytes = self._parse_storage_to_bytes(today_used_str)
            remaining_bytes = self._parse_storage_to_bytes(remaining_str)
            total_bytes = used_total_bytes + remaining_bytes

            # 5. 解析 VIP 到期时间
            expiry_date = self._parse_expiry(html)

            return [
                CollectResult(
                    source="clash",
                    metric="traffic",
                    value=remaining_bytes,
                    unit="bytes",
                    detail={
                        "used_total_bytes": used_total_bytes,
                        "used_total_str": used_total_str,
                        "today_used_bytes": today_used_bytes,
                        "today_used_str": today_used_str,
                        "remaining_bytes": remaining_bytes,
                        "remaining_str": remaining_str,
                        "total_bytes": total_bytes,
                        "used_percent": used_percent,
                        "today_percent": today_percent,
                        "remaining_percent": remaining_percent,
                        "used_bandwidth": round(used_total_bytes / (1024 ** 3), 2),
                        "remaining_bandwidth": round(remaining_bytes / (1024 ** 3), 2),
                        "total_bandwidth": round(total_bytes / (1024 ** 3), 2),
                        "expiry": expiry_date,
                        "unit": "GB",
                    },
                ),
            ]

    # ------------------------------------------------------------------
    # 解析辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_traffic_data(html: str) -> dict[str, Any] | None:
        """从 HTML 中解析 trafficDountChat() 的调用参数。

        页面中的调用格式:
            trafficDountChat('33.37GB', '48.94MB', '64.82GB', '33.97', '0.05', '65.98')
        参数顺序: dm(已用总计), today(今日已用), exp(可用流量),
                  sm(已用%), td(今日%), px(可用%)
        """
        pattern = (
            r"trafficDountChat\s*\(\s*"
            r"['\"]([^'\"]+)['\"]\s*,\s*"   # arg0: 已用总计 "33.37GB"
            r"['\"]([^'\"]+)['\"]\s*,\s*"   # arg1: 今日已用 "48.94MB"
            r"['\"]([^'\"]+)['\"]\s*,\s*"   # arg2: 可用流量 "64.82GB"
            r"['\"]([^'\"]+)['\"]\s*,\s*"   # arg3: 已用百分比 "33.97"
            r"['\"]([^'\"]+)['\"]\s*,\s*"   # arg4: 今日百分比 "0.05"
            r"['\"]([^'\"]+)['\"]"           # arg5: 可用百分比 "65.98"
            r"\s*\)"
        )
        match = re.search(pattern, html)
        if not match:
            return None

        return {
            "used_total_str": match.group(1),
            "today_used_str": match.group(2),
            "remaining_str": match.group(3),
            "used_percent": float(match.group(4)),
            "today_percent": float(match.group(5)),
            "remaining_percent": float(match.group(6)),
        }

    @staticmethod
    def _parse_storage_to_bytes(value: str) -> int:
        """将存储容量字符串转换为字节数。如 "33.37GB" → 35832000000"""
        value = value.strip().upper()
        units = {"B": 1, "KB": 1024, "MB": 1024 ** 2,
                 "GB": 1024 ** 3, "TB": 1024 ** 4}
        for unit, multiplier in sorted(units.items(), key=lambda x: -len(x[0])):
            if value.endswith(unit):
                number = float(value[: -len(unit)])
                return int(number * multiplier)
        # 无单位默认当作字节
        return int(float(value))

    @staticmethod
    def _parse_expiry(html: str) -> str:
        """从 HTML 中尝试解析 VIP 到期时间。

        页面中可能有多种格式:
            - "vip-time": "2026-10-22 09:20:12" (Crisp session:data)
            - "会员时长：128 天（到期 2026-10-22）"
        """
        # 尝试匹配 Crisp session:data 中的 vip-time
        m = re.search(r'"vip-time"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)[:10]  # "2026-10-22"

        # 尝试匹配 "到期 XXXX-XX-XX" 格式
        m = re.search(r'到期\s*(\d{4}-\d{2}-\d{2})', html)
        if m:
            return m.group(1)

        # 尝试匹配页面上的 "会员时长" 信息
        m = re.search(r'(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}', html)
        if m:
            return m.group(1)

        return "待确认"
