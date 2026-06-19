"""定时任务调度"""
from collections.abc import Awaitable, Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


class Scheduler:
    """管理定时采集任务"""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._job_id = "collect"
        self._collect_fn: Callable[[], Awaitable[None]] | None = None

    def start(self, collect_fn: Callable[[], Awaitable[None]], interval_minutes: int = 5) -> None:
        """启动调度器"""
        self._collect_fn = collect_fn
        self._scheduler.add_job(
            collect_fn,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id=self._job_id,
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.start()

    def change_interval(self, minutes: int) -> None:
        """动态调整采集间隔"""
        if minutes < 1:
            minutes = 1
        self._scheduler.reschedule_job(
            self._job_id,
            trigger=IntervalTrigger(minutes=minutes),
        )

    def stop(self) -> None:
        """停止调度器"""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
