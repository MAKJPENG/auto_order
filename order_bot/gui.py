from __future__ import annotations

import argparse
import csv
import queue
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext, ttk

from .audit import AuditLogger
from .browser_client import BrowserOrderClient, DryRunOrderClient, result_to_dict
from .csv_loader import load_orders
from .models import Order, OrderAttemptResult, ScheduleEntry
from .paths import log_dir
from .scheduler import build_schedule, save_schedule
from .time_utils import get_timezone, parse_clock, timezone_label


STATUS_PENDING = "等待下单"
STATUS_RUNNING = "正在下单"
STATUS_DONE = "下单完成"
STATUS_FILLED = "已填写待提交"
STATUS_DRY_RUN = "演练完成"
STATUS_SKIPPED = "已跳过"
STATUS_FAILED = "下单失败"
STATUS_CANCELLED = "已停止"

FAILED_ROW_TAG = "failed"
ERROR_LOG_TAG = "error"
ERROR_LOG_KEYWORDS = ("失败", "错误", "出错", "异常", "Traceback", "Error", "Exception", "failed", "failure")


@dataclass
class RowState:
    entry: ScheduleEntry
    item_id: str
    row_key: str
    status: str = STATUS_PENDING
    message: str = ""


class ModeSelectionApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("自动下单机器人")
        self.root.geometry("420x220")
        self.root.minsize(360, 180)
        self._build_layout()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Button(outer, text="去下单", command=self._open_order_app).grid(
            row=0,
            column=0,
            sticky="nsew",
            pady=(0, 12),
        )
        ttk.Button(outer, text="发邮件", command=self._open_email_app).grid(
            row=1,
            column=0,
            sticky="nsew",
        )

    def _open_order_app(self) -> None:
        clear_root(self.root)
        OrderBotApp(self.root)

    def _open_email_app(self) -> None:
        clear_root(self.root)
        EmailApp(self.root)


class EmailApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("自动发送邮件")
        self.root.geometry("760x480")
        self.root.minsize(560, 360)
        self._build_layout()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(
            outer,
            text="邮件功能界面已预留，后续会在这里配置邮箱、模板和发送任务。",
            anchor="center",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 16))
        ttk.Frame(outer).grid(row=1, column=0, sticky="nsew")
        ttk.Button(outer, text="返回", command=self._back).grid(row=2, column=0, sticky="e")

    def _back(self) -> None:
        clear_root(self.root)
        ModeSelectionApp(self.root)


class OrderBotApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("自动下单机器人")
        self.root.geometry("1280x760")
        self.root.minsize(980, 620)

        self.csv_path = StringVar()
        self.days = IntVar(value=3)
        self.window_start = StringVar(value="09:00")
        self.window_end = StringVar(value="22:00")
        self.mode = StringVar(value="browser")
        self.payment_method = StringVar(value="bank_transfer")
        self.submit_final = BooleanVar(value=True)
        self.keep_open_on_failure = BooleanVar(value=False)
        self.allow_detected_country_on_mismatch = BooleanVar(value=False)
        self.use_country_timezone = BooleanVar(value=True)
        self.past_policy = StringVar(value="skip")
        self.review_seconds = IntVar(value=120)
        self.status_text = StringVar(value="请选择订单 CSV 文件")
        self.progress_text = StringVar(value="0/0")

        self.tz = get_timezone("Asia/Shanghai")
        self.rows: list[RowState] = []
        self.table_columns: list[str] = []
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.logs_dir = log_dir()
        self.audit = AuditLogger(self.logs_dir / "orders.jsonl")

        self._build_layout()
        self._poll_events()
        self._tick_countdowns()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        config = ttk.LabelFrame(outer, text="配置")
        config.pack(fill="x")

        ttk.Label(config, text="订单数据文件").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(config, textvariable=self.csv_path).grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        ttk.Button(config, text="选择文件", command=self.choose_file).grid(row=0, column=2, padx=8, pady=8)

        ttk.Label(config, text="随机分配天数").grid(row=1, column=0, sticky="w", padx=8, pady=8)
        schedule_frame = ttk.Frame(config)
        schedule_frame.grid(row=1, column=1, columnspan=2, sticky="w", padx=4, pady=8)
        ttk.Spinbox(schedule_frame, from_=1, to=365, textvariable=self.days, width=8).pack(side="left")
        ttk.Label(schedule_frame, text="随机时间段").pack(side="left", padx=(18, 6))
        ttk.Entry(schedule_frame, textvariable=self.window_start, width=8).pack(side="left", padx=(0, 4))
        ttk.Label(schedule_frame, text="到").pack(side="left")
        ttk.Entry(schedule_frame, textvariable=self.window_end, width=8).pack(side="left", padx=4)

        ttk.Label(config, text="执行模式").grid(row=2, column=0, sticky="w", padx=8, pady=8)
        exec_frame = ttk.Frame(config)
        exec_frame.grid(row=2, column=1, sticky="w", padx=4, pady=8)
        mode_box = ttk.Combobox(
            exec_frame,
            textvariable=self.mode,
            state="readonly",
            width=22,
            values=("browser", "dry-run"),
        )
        mode_box.pack(side="left")
        ttk.Checkbutton(
            exec_frame,
            text="自动点击下单",
            variable=self.submit_final,
        ).pack(side="left", padx=(14, 0))

        option_frame = ttk.Frame(config)
        option_frame.grid(row=2, column=2, sticky="e", padx=8, pady=8)
        ttk.Label(option_frame, text="支付方式").pack(side="left")
        ttk.Combobox(
            option_frame,
            textvariable=self.payment_method,
            state="readonly",
            width=16,
            values=("bank_transfer", "popular_payments"),
        ).pack(side="left", padx=(8, 14))
        ttk.Checkbutton(
            option_frame,
            text="按国家时区下单",
            variable=self.use_country_timezone,
        ).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(
            option_frame,
            text="失败时保留浏览器",
            variable=self.keep_open_on_failure,
        ).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(
            option_frame,
            text="国家搜不到用网站国家",
            variable=self.allow_detected_country_on_mismatch,
        ).pack(side="left", padx=(0, 14))
        ttk.Label(option_frame, text="过期 run_at").pack(side="left")
        ttk.Combobox(
            option_frame,
            textvariable=self.past_policy,
            state="readonly",
            width=10,
            values=("skip", "run-now", "error"),
        ).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(config)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(4, 10))
        ttk.Button(actions, text="预览排期", command=self.preview_schedule).pack(side="left")
        self.start_button = ttk.Button(actions, text="开始下单", command=self.start_orders)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止等待", command=self.stop_orders, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Button(actions, text="导出订单进度", command=self.export_progress).pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.status_text).pack(side="left", padx=18)
        ttk.Label(actions, textvariable=self.progress_text).pack(side="right")
        self.progress = ttk.Progressbar(actions, mode="determinate", length=260)
        self.progress.pack(side="right", padx=8)

        config.columnconfigure(1, weight=1)

        table_frame = ttk.LabelFrame(outer, text="订单进度")
        table_frame.pack(fill="both", expand=True, pady=(12, 8))
        self.table = ttk.Treeview(table_frame, show="headings")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.table.tag_configure(FAILED_ROW_TAG, foreground="#b00020")
        self.table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(outer, text="运行日志")
        log_frame.pack(fill="x")
        self.log = scrolledtext.ScrolledText(log_frame, height=7, state="disabled")
        self.log.tag_configure(ERROR_LOG_TAG, foreground="#b00020")
        self.log.pack(fill="both", expand=True)

    def choose_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择订单 CSV 文件",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if filename:
            self.csv_path.set(filename)
            self._clear_entries()
            self.status_text.set("已选择订单 CSV，点击开始下单后生成随机排期")
            self._append_log("已选择订单 CSV，等待开始下单")

    def preview_schedule(self) -> None:
        try:
            entries = self._load_schedule()
        except Exception as exc:
            self.status_text.set("排期失败")
            self._append_log(f"排期失败：{exc}", level=ERROR_LOG_TAG)
            return

        self._display_entries(entries)
        schedule_path = self.logs_dir / "schedule.csv"
        try:
            save_schedule(entries, schedule_path)
        except Exception as exc:
            self.status_text.set("排期保存失败")
            self._append_log(f"排期保存失败：{exc}", level=ERROR_LOG_TAG)
            return
        self.status_text.set(f"已生成排期，共 {len(entries)} 条")
        self._append_log(f"排期已生成：{schedule_path}")

    def start_orders(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "下单任务已经在运行中")
            return

        try:
            entries = self._load_schedule()
        except Exception as exc:
            self.status_text.set("无法开始")
            self._append_log(f"无法开始：{exc}", level=ERROR_LOG_TAG)
            return

        self._display_entries(entries)
        schedule_path = self.logs_dir / "schedule.csv"
        try:
            save_schedule(entries, schedule_path)
        except Exception as exc:
            self.status_text.set("排期保存失败")
            self._append_log(f"排期保存失败，任务未启动：{exc}", level=ERROR_LOG_TAG)
            return
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_text.set("任务已启动，等待下单时间")
        self._append_log(f"排期已在开始下单时生成：{schedule_path}")
        self._append_log("任务启动")

        worker_args = {
            "entries": entries,
            "mode": self.mode.get(),
            "submit_final": self.submit_final.get(),
            "payment_method": self.payment_method.get(),
            "keep_open_on_failure": self.keep_open_on_failure.get(),
            "allow_detected_country_on_mismatch": self.allow_detected_country_on_mismatch.get(),
            "past_policy": self.past_policy.get(),
            "review_seconds": self.review_seconds.get(),
        }
        self.worker = threading.Thread(target=self._run_worker, kwargs=worker_args, daemon=True)
        self.worker.start()

    def stop_orders(self) -> None:
        self.stop_event.set()
        self.status_text.set("正在停止等待，当前下单动作会先结束")
        self._append_log("收到停止请求")

    def export_progress(self) -> None:
        if not self.rows or not self.table_columns:
            messagebox.showinfo("没有可导出数据", "当前没有订单进度，请先预览排期或开始下单。")
            return

        default_name = f"order-progress-{datetime.now(self.tz).strftime('%Y%m%d-%H%M%S')}.csv"
        filename = filedialog.asksaveasfilename(
            title="导出订单进度",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not filename:
            return

        path = Path(filename)
        try:
            self._write_progress_csv(path)
        except Exception as exc:
            self.status_text.set("订单进度导出失败")
            self._append_log(f"订单进度导出失败：{exc}", level=ERROR_LOG_TAG)
            return

        self.status_text.set(f"订单进度已导出：{path}")
        self._append_log(f"订单进度已导出：{path}")

    def _write_progress_csv(self, path: Path) -> None:
        headers, rows = self._progress_export_data()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)

    def _progress_export_data(self) -> tuple[list[str], list[list[str]]]:
        headers = []
        for column in self.table_columns:
            try:
                heading = self.table.heading(column, option="text")
            except Exception:
                heading = ""
            headers.append(heading or column)
        return headers, [self._row_values(row) for row in self.rows]

    def _load_schedule(self) -> list[ScheduleEntry]:
        csv_file = Path(self.csv_path.get().strip())
        if not csv_file:
            raise ValueError("请选择订单 CSV 文件。")
        if not csv_file.exists():
            raise ValueError(f"文件不存在：{csv_file}")

        orders = load_orders(csv_file, self.tz, use_country_timezone=self.use_country_timezone.get())
        return build_schedule(
            orders,
            spread_days=int(self.days.get()),
            tz=self.tz,
            window_start=parse_clock(self.window_start.get()),
            window_end=parse_clock(self.window_end.get()),
        )

    def _display_entries(self, entries: list[ScheduleEntry]) -> None:
        self.rows.clear()
        self.table.delete(*self.table.get_children())
        computed_columns = {"status", "countdown", "scheduled_at", "timezone", "source", "message"}
        raw_columns = [column for column in entries[0].order.raw.keys() if column not in computed_columns] if entries else []
        self.table_columns = [
            "status",
            "countdown",
            "scheduled_at",
            "timezone",
            "source",
            "message",
            *raw_columns,
        ]
        self.table.configure(columns=self.table_columns)

        headings = {
            "status": "状态",
            "countdown": "下单倒计时",
            "scheduled_at": "计划下单时间",
            "timezone": "当地时区",
            "source": "时间来源",
            "message": "执行信息",
        }
        widths = {
            "status": 100,
            "countdown": 120,
            "scheduled_at": 260,
            "timezone": 170,
            "source": 80,
            "message": 220,
            "product_url": 360,
            "address_line": 220,
            "email": 210,
            "notes": 160,
        }
        for column in self.table_columns:
            self.table.heading(column, text=headings.get(column, column))
            self.table.column(column, width=widths.get(column, 140), minwidth=80, stretch=True)

        for index, entry in enumerate(entries):
            row = RowState(entry=entry, item_id="", row_key=f"row-{index}")
            values = self._row_values(row)
            item_id = self.table.insert("", "end", values=values, tags=self._row_tags(row))
            row.item_id = item_id
            self.rows.append(row)

        self._refresh_progress()

    def _clear_entries(self) -> None:
        self.rows.clear()
        self.table_columns = []
        self.table.delete(*self.table.get_children())
        self.progress.configure(maximum=1, value=0)
        self.progress_text.set("0/0")

    def _run_worker(
        self,
        *,
        entries: list[ScheduleEntry],
        mode: str,
        submit_final: bool,
        payment_method: str,
        keep_open_on_failure: bool,
        allow_detected_country_on_mismatch: bool,
        past_policy: str,
        review_seconds: int,
    ) -> None:
        client = (
            BrowserOrderClient(
                review_seconds=review_seconds,
                payment_method=payment_method,
                keep_open_on_failure=keep_open_on_failure,
                allow_detected_country_on_mismatch=allow_detected_country_on_mismatch,
                log_callback=lambda message: self._emit("browser_log", message=message),
            )
            if mode == "browser"
            else DryRunOrderClient()
        )
        try:
            for index, entry in enumerate(entries):
                row_key = f"row-{index}"
                if self.stop_event.is_set():
                    self._emit("cancelled", row_key=row_key, order_id=entry.order.order_id, message="用户停止等待")
                    continue

                now = datetime.now(entry.scheduled_at.tzinfo)
                if entry.scheduled_at < now:
                    if past_policy == "skip":
                        self._emit(
                            "skipped",
                            row_key=row_key,
                            order_id=entry.order.order_id,
                            message="run_at 已过期，已跳过",
                        )
                        continue
                    if past_policy == "error":
                        self._emit(
                            "failed",
                            row_key=row_key,
                            order_id=entry.order.order_id,
                            message=(
                                "run_at 已过期："
                                f"{entry.scheduled_at.isoformat(sep=' ', timespec='seconds')}"
                            ),
                        )
                        continue

                self._wait_until(entry.scheduled_at, row_key, entry.order.order_id)
                if self.stop_event.is_set():
                    self._emit("cancelled", row_key=row_key, order_id=entry.order.order_id, message="用户停止等待")
                    continue

                self._emit("running", row_key=row_key, order_id=entry.order.order_id, message="正在下单")
                result = self._place_order(client, entry.order, mode, submit_final)
                self._record_audit_safely(entry, result)
                event = "done" if result.success else "failed"
                self._emit(
                    event,
                    row_key=row_key,
                    order_id=entry.order.order_id,
                    message=result.message,
                    submitted=result.submitted,
                    mode=mode,
                )
        except Exception as exc:
            self._emit("fatal", message=str(exc), traceback=traceback.format_exc())
        finally:
            self._emit("worker_done")

    def _record_audit_safely(self, entry: ScheduleEntry, result: OrderAttemptResult) -> None:
        try:
            self.audit.record(
                "order_attempt",
                order_id=entry.order.order_id,
                scheduled_at=entry.scheduled_at.isoformat(),
                result=result_to_dict(result),
            )
        except Exception as exc:
            self._emit("browser_log", message=f"审计日志写入失败，不影响继续下单：{exc}")

    def _place_order(
        self,
        client: BrowserOrderClient | DryRunOrderClient,
        order: Order,
        mode: str,
        submit_final: bool,
    ) -> OrderAttemptResult:
        try:
            if mode == "browser":
                return client.place_order(order, submit_final=submit_final)
            return client.place_order(order)
        except Exception as exc:
            return OrderAttemptResult(
                False,
                False,
                self._format_order_exception(exc),
                {
                    "order_id": order.order_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

    def _format_order_exception(self, exc: Exception) -> str:
        text = str(exc).strip()
        if not text:
            return f"下单过程中出现异常：{type(exc).__name__}"
        return f"下单过程中出现异常：{text}"

    def _wait_until(self, target: datetime, row_key: str, order_id: str) -> None:
        while not self.stop_event.is_set():
            now = datetime.now(target.tzinfo)
            if now >= target:
                return
            remaining = max(0, int((target - now).total_seconds()))
            self._emit("waiting", row_key=row_key, order_id=order_id, message=f"距离下单 {format_seconds(remaining)}")
            self.stop_event.wait(min(1, remaining))

    def _emit(self, event: str, **payload) -> None:
        self.events.put((event, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_events)

    def _handle_event(self, event: str, payload: dict) -> None:
        row_key = payload.get("row_key")
        order_id = payload.get("order_id")
        message = payload.get("message", "")

        if event == "waiting":
            self.status_text.set(message)
            return

        if event == "running":
            self._set_row_status(row_key, order_id, STATUS_RUNNING, message)
            self.status_text.set(f"{order_id} 正在下单")
            self._append_log(f"{order_id}: 正在下单")
        elif event == "done":
            submitted = payload.get("submitted")
            if submitted:
                status = STATUS_DONE
            elif payload.get("mode") == "browser":
                status = STATUS_FILLED
            else:
                status = STATUS_DRY_RUN
            self._set_row_status(row_key, order_id, status, message)
            self.status_text.set(f"{order_id} {status}")
            self._append_log(f"{order_id}: {status}，{message}")
        elif event == "failed":
            self._set_row_status(row_key, order_id, STATUS_FAILED, message)
            self.status_text.set(f"{order_id} 下单失败")
            self._append_log(f"{order_id}: 下单失败，{message}")
        elif event == "skipped":
            self._set_row_status(row_key, order_id, STATUS_SKIPPED, message)
            self._append_log(f"{order_id}: {message}")
        elif event == "cancelled":
            self._set_row_status(row_key, order_id, STATUS_CANCELLED, message)
            self._append_log(f"{order_id}: {message}")
        elif event == "browser_log":
            self._append_log(message)
        elif event == "fatal":
            self.status_text.set("任务出错")
            self._append_log(f"任务出错：{message}", level=ERROR_LOG_TAG)
            if payload.get("traceback"):
                self._append_log(payload["traceback"], level=ERROR_LOG_TAG)
        elif event == "worker_done":
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            if not self.stop_event.is_set() and self.status_text.get() != "任务出错":
                self.status_text.set("任务结束")

        self._refresh_progress()

    def _set_row_status(self, row_key: str | None, order_id: str | None, status: str, message: str) -> None:
        row = self._find_row(row_key, order_id)
        if row is None:
            return
        row.status = status
        row.message = message
        self.table.item(row.item_id, values=self._row_values(row), tags=self._row_tags(row))

    def _find_row(self, row_key: str | None, order_id: str | None) -> RowState | None:
        if row_key:
            for row in self.rows:
                if row.row_key == row_key:
                    return row
        if not order_id:
            return None
        for row in self.rows:
            if row.entry.order.order_id == order_id and row.status in {STATUS_PENDING, STATUS_RUNNING}:
                return row
        return None

    def _tick_countdowns(self) -> None:
        for row in self.rows:
            if row.status in {STATUS_PENDING, STATUS_RUNNING}:
                self.table.item(row.item_id, values=self._row_values(row), tags=self._row_tags(row))
        self.root.after(1000, self._tick_countdowns)

    def _row_values(self, row: RowState) -> list[str]:
        order = row.entry.order
        computed = {
            "status": row.status,
            "countdown": self._countdown_text(row),
            "scheduled_at": self._format_scheduled_at(row.entry.scheduled_at),
            "timezone": self._format_timezone(row.entry.scheduled_at),
            "source": row.entry.source,
            "message": row.message,
        }
        return [computed.get(column, order.raw.get(column, "")) for column in self.table_columns]

    def _row_tags(self, row: RowState) -> tuple[str, ...]:
        if row.status == STATUS_FAILED:
            return (FAILED_ROW_TAG,)
        return ()

    def _format_scheduled_at(self, scheduled_at: datetime) -> str:
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=self.tz)
        return scheduled_at.strftime("%Y-%m-%d %H:%M:%S")

    def _format_timezone(self, scheduled_at: datetime) -> str:
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=self.tz)
        offset = scheduled_at.utcoffset()
        label = timezone_label(scheduled_at.tzinfo)
        if offset is None:
            return label
        total_minutes = int(offset.total_seconds() / 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hours, minutes = divmod(total_minutes, 60)
        return f"({sign}{hours:02d}:{minutes:02d}) {label}"

    def _countdown_text(self, row: RowState) -> str:
        if row.status in {
            STATUS_DONE,
            STATUS_FILLED,
            STATUS_DRY_RUN,
            STATUS_SKIPPED,
            STATUS_FAILED,
            STATUS_CANCELLED,
        }:
            return "-"
        if row.status == STATUS_RUNNING:
            return "正在下单"
        remaining = int((row.entry.scheduled_at - datetime.now(row.entry.scheduled_at.tzinfo)).total_seconds())
        if remaining <= 0:
            return "到点"
        return format_seconds(remaining)

    def _refresh_progress(self) -> None:
        total = len(self.rows)
        finished = sum(
            row.status
            in {
                STATUS_DONE,
                STATUS_FILLED,
                STATUS_DRY_RUN,
                STATUS_SKIPPED,
                STATUS_FAILED,
                STATUS_CANCELLED,
            }
            for row in self.rows
        )
        self.progress.configure(maximum=max(1, total), value=finished)
        self.progress_text.set(f"{finished}/{total}")

    def _append_log(self, message: str, *, level: str | None = None) -> None:
        timestamp = datetime.now(self.tz).strftime("%H:%M:%S")
        tag = ERROR_LOG_TAG if level == ERROR_LOG_TAG or self._is_error_log_message(message) else None
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", f"[{timestamp}] {message}\n", tag)
        else:
            self.log.insert("end", f"[{timestamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _is_error_log_message(self, message: str) -> bool:
        lowered = (message or "").casefold()
        return any(keyword.casefold() in lowered for keyword in ERROR_LOG_KEYWORDS)


def format_seconds(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def clear_root(root: Tk) -> None:
    for child in root.winfo_children():
        child.destroy()


def parse_gui_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the order bot desktop UI.")
    parser.add_argument("--self-test", action="store_true", help="Import-check only.")
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_gui_args(argv)
    if args.self_test:
        return 0

    root = Tk()
    ModeSelectionApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
