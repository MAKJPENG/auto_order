from __future__ import annotations

import time
from datetime import datetime

from .audit import AuditLogger
from .browser_client import result_to_dict
from .models import OrderAttemptResult, ScheduleEntry


def run_schedule(
    entries: list[ScheduleEntry],
    *,
    client,
    audit: AuditLogger,
    past_policy: str,
    poll_seconds: int = 30,
) -> None:
    for entry in entries:
        now = datetime.now(entry.scheduled_at.tzinfo)
        if entry.scheduled_at < now:
            if past_policy == "skip":
                message = "scheduled time is in the past; skipped"
                print(f"[SKIP] {entry.order.order_id}: {message}")
                audit.record(
                    "order_skipped",
                    order_id=entry.order.order_id,
                    scheduled_at=entry.scheduled_at.isoformat(),
                    reason=message,
                )
                continue
            if past_policy == "error":
                raise RuntimeError(
                    f"{entry.order.order_id} scheduled time is in the past: "
                    f"{entry.scheduled_at.isoformat()}"
                )

        wait_until(entry.scheduled_at, poll_seconds=poll_seconds)
        print(f"[RUN] {entry.order.order_id}: {entry.order.product_url}")
        try:
            result = client.place_order(entry.order)
        except Exception as exc:
            result = OrderAttemptResult(False, False, str(exc))

        audit.record(
            "order_attempt",
            order_id=entry.order.order_id,
            scheduled_at=entry.scheduled_at.isoformat(),
            result=result_to_dict(result),
        )
        status = "OK" if result.success else "FAIL"
        print(f"[{status}] {entry.order.order_id}: {result.message}")


def wait_until(target: datetime, *, poll_seconds: int) -> None:
    while True:
        now = datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(max(1, poll_seconds), remaining))

