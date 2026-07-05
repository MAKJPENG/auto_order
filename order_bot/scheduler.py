from __future__ import annotations

import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .models import Order, ScheduleEntry
from .time_utils import get_timezone, timezone_label


def build_schedule(
    orders: list[Order],
    *,
    spread_days: int,
    tz,
    start_date: date | None = None,
    now: datetime | None = None,
    window_start: time = time(0, 0),
    window_end: time = time(23, 59, 59),
    seed: int | None = None,
) -> list[ScheduleEntry]:
    if spread_days < 1:
        raise ValueError("spread_days must be at least 1.")
    if window_end <= window_start:
        raise ValueError("window_end must be later than window_start.")

    base_current = now or datetime.now(tz)
    if base_current.tzinfo is None:
        base_current = base_current.replace(tzinfo=tz)

    fixed_entries = [
        ScheduleEntry(order=order, scheduled_at=order.run_at, source="run_at")
        for order in orders
        if order.run_at is not None
    ]
    pending_orders = [order for order in orders if order.run_at is None]

    rng = random.Random(seed)
    shuffled = list(pending_orders)
    rng.shuffle(shuffled)

    used_clock_times = {_clock_key(entry.scheduled_at) for entry in fixed_entries}
    random_entries: list[ScheduleEntry] = []
    for index, order in enumerate(shuffled):
        order_tz = _order_timezone(order, tz)
        current = base_current.astimezone(order_tz)
        start = start_date or current.date()
        day_offset = index % spread_days
        scheduled_date = start + timedelta(days=day_offset)
        start_dt = datetime.combine(scheduled_date, window_start, tzinfo=order_tz)
        end_dt = datetime.combine(scheduled_date, window_end, tzinfo=order_tz)

        if scheduled_date == current.date() and start_dt <= current:
            start_dt = (current + timedelta(seconds=60)).replace(microsecond=0)
        if start_dt > end_dt:
            raise ValueError(
                f"No valid time window remains on {scheduled_date} in {timezone_label(order_tz)}. "
                "Use a later --start-date or a wider window."
            )

        scheduled_at = _pick_unique_datetime(start_dt, end_dt, rng, used_clock_times)
        random_entries.append(
            ScheduleEntry(order=order, scheduled_at=scheduled_at, source="random")
        )

    return sorted([*fixed_entries, *random_entries], key=lambda entry: entry.scheduled_at)


def save_schedule(entries: list[ScheduleEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "order_id",
                "scheduled_at",
                "timezone",
                "source",
                "product_url",
                "quantity",
                "email",
                "full_name",
                "phone",
                "payment_method",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry.order.order_id,
                    entry.scheduled_at.isoformat(sep=" ", timespec="seconds"),
                    timezone_label(entry.scheduled_at.tzinfo),
                    entry.source,
                    entry.order.product_url,
                    entry.order.quantity,
                    entry.order.email,
                    entry.order.full_name,
                    entry.order.phone,
                    entry.order.payment_method,
                ]
            )


def format_schedule(entries: list[ScheduleEntry]) -> str:
    lines = [
        "order_id | scheduled_at | timezone | source | quantity | product_url",
        "-" * 88,
    ]
    for entry in entries:
        lines.append(
            " | ".join(
                [
                    entry.order.order_id,
                    entry.scheduled_at.isoformat(sep=" ", timespec="seconds"),
                    timezone_label(entry.scheduled_at.tzinfo),
                    entry.source,
                    str(entry.order.quantity),
                    entry.order.product_url,
                ]
            )
        )
    return "\n".join(lines)


def _pick_unique_datetime(
    start_dt: datetime,
    end_dt: datetime,
    rng: random.Random,
    used_clock_times: set[tuple[str, str]],
) -> datetime:
    total_seconds = int((end_dt - start_dt).total_seconds())
    if total_seconds < 0:
        raise ValueError("Invalid scheduling range.")

    for _ in range(2000):
        candidate = start_dt + timedelta(seconds=rng.randint(0, total_seconds))
        candidate = candidate.replace(microsecond=0)
        clock = _clock_key(candidate)
        if clock not in used_clock_times:
            used_clock_times.add(clock)
            return candidate

    for offset in range(total_seconds + 1):
        candidate = (start_dt + timedelta(seconds=offset)).replace(microsecond=0)
        clock = _clock_key(candidate)
        if clock not in used_clock_times:
            used_clock_times.add(clock)
            return candidate

    raise ValueError("Not enough unique HH:MM:SS values in the scheduling window.")


def _order_timezone(order: Order, default_tz):
    return get_timezone(order.time_zone) if order.time_zone else default_tz


def _clock_key(value: datetime) -> tuple[str, str]:
    return timezone_label(value.tzinfo), value.strftime("%H:%M:%S")
