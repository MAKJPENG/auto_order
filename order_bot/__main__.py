from __future__ import annotations

import argparse
from pathlib import Path

from .audit import AuditLogger
from .browser_client import BrowserOrderClient, DryRunOrderClient
from .csv_loader import load_orders
from .runner import run_schedule
from .scheduler import build_schedule, format_schedule, save_schedule
from .time_utils import get_timezone, parse_clock, parse_date


def main() -> int:
    parser = argparse.ArgumentParser(description="CSV-driven scheduled order bot.")
    parser.add_argument("--csv", required=True, type=Path, help="订单 CSV 文件路径")
    parser.add_argument("--spread-days", type=int, default=1, help="无 run_at 订单分散的天数")
    parser.add_argument("--start-date", help="随机排期开始日期，例如 2026-07-05")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="时区，默认 Asia/Shanghai")
    parser.add_argument(
        "--no-country-timezone",
        action="store_false",
        dest="use_country_timezone",
        help="关闭按国家自动匹配当地时区，改用 --timezone 指定的统一时区",
    )
    parser.add_argument("--window-start", default="00:00", help="随机时间窗口开始，例如 09:00")
    parser.add_argument("--window-end", default="23:59:59", help="随机时间窗口结束，例如 22:00")
    parser.add_argument("--seed", type=int, help="随机种子，用于复现同一份排期")
    parser.add_argument("--schedule-output", type=Path, default=Path("logs/schedule.csv"))
    parser.add_argument("--audit-log", type=Path, default=Path("logs/orders.jsonl"))
    parser.add_argument("--run", action="store_true", help="等待排期时间并执行")
    parser.add_argument("--mode", choices=["dry-run", "browser"], default="dry-run")
    parser.add_argument("--payment-method", default="bank_transfer", help="payment method, default bank_transfer")
    parser.add_argument(
        "--submit-final",
        action="store_true",
        help="浏览器模式下点击最终下单按钮",
    )
    parser.add_argument(
        "--keep-open-on-failure",
        action="store_true",
        help="下单失败时保留浏览器窗口用于排查",
    )
    parser.add_argument(
        "--allow-detected-country-on-mismatch",
        action="store_true",
        help="CSV 国家在结账页搜不到时，使用网站自动匹配的国家继续下单",
    )
    parser.add_argument("--headless", action="store_true", help="无界面浏览器")
    parser.add_argument("--slow-mo-ms", type=int, default=0, help="浏览器动作减速毫秒数")
    parser.add_argument(
        "--review-seconds",
        type=int,
        default=120,
        help="未提交前保留浏览器页面的秒数",
    )
    parser.add_argument(
        "--past-policy",
        choices=["skip", "run-now", "error"],
        default="skip",
        help="遇到过去的 run_at 时如何处理",
    )
    args = parser.parse_args()

    tz = get_timezone(args.timezone)
    orders = load_orders(args.csv, tz, use_country_timezone=args.use_country_timezone)
    entries = build_schedule(
        orders,
        spread_days=args.spread_days,
        tz=tz,
        start_date=parse_date(args.start_date) if args.start_date else None,
        window_start=parse_clock(args.window_start),
        window_end=parse_clock(args.window_end),
        seed=args.seed,
    )
    save_schedule(entries, args.schedule_output)

    print(format_schedule(entries))
    print(f"\nSchedule saved to: {args.schedule_output}")

    if not args.run:
        print("Plan-only mode. Add --run to wait and execute.")
        return 0

    if args.mode == "browser":
        client = BrowserOrderClient(
            headless=args.headless,
            slow_mo_ms=args.slow_mo_ms,
            review_seconds=args.review_seconds,
            payment_method=args.payment_method,
            keep_open_on_failure=args.keep_open_on_failure,
            allow_detected_country_on_mismatch=args.allow_detected_country_on_mismatch,
        )
        if args.submit_final:
            original_place_order = client.place_order

            def place_order_with_submit(order):
                return original_place_order(order, submit_final=True)

            client.place_order = place_order_with_submit
    else:
        client = DryRunOrderClient()

    run_schedule(
        entries,
        client=client,
        audit=AuditLogger(args.audit_log),
        past_policy=args.past_policy,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
