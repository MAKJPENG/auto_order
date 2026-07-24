from __future__ import annotations

import argparse
import csv
import os
import queue
import threading
import traceback
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, Toplevel, filedialog, messagebox
from tkinter import scrolledtext, ttk

from .audit import AuditLogger
from .browser_client import BrowserOrderClient, DryRunOrderClient, result_to_dict
from .csv_loader import load_orders
from .email_accounts import (
    EmailAccountStore,
    EmailLoginError,
    make_login_info,
    provider_names,
    resolve_provider_settings,
    security_options,
    verify_smtp_login,
)
from .email_tasks import (
    EmailTask,
    build_email_tasks,
    compose_email_message,
    default_email_subject,
    send_email_message,
)
from .email_preview import build_email_preview_page
from .email_templates import (
    EMAIL_TYPE_CUSTOM,
    EMAIL_TYPE_ORDER_CONFIRMATION,
    EMAIL_TYPE_SHIPPING_CONFIRMATION,
    EMAIL_TYPE_VAT_INVOICE,
    email_type_names,
    placeholder_hint,
    validate_email_task,
)
from .invoice_generator import (
    INVOICE_OUTPUT_FORMAT_LABELS,
    INVOICE_OUTPUT_FORMAT_PDF,
    InvoiceDataRow,
    analyze_invoice_template,
    generate_invoice_file,
    invoice_output_extension,
    invoice_output_format_from_label,
    invoice_output_format_labels,
    load_invoice_rows,
    normalize_company_key,
    output_invoice_path,
    unique_companies,
)
from .models import Order, OrderAttemptResult, ScheduleEntry
from .paths import install_preview_dir, invoice_preview_dir, log_dir
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

EMAIL_STATUS_PENDING = "待发送"
EMAIL_STATUS_WAITING = "等待发送"
EMAIL_STATUS_SENDING = "进行中"
EMAIL_STATUS_SENT = "发送完成"
EMAIL_STATUS_FAILED = "发送失败"
EMAIL_STATUS_CANCELLED = "已停止"
EMAIL_FINISHED_STATUSES = {EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED, EMAIL_STATUS_CANCELLED}

INVOICE_STATUS_PENDING = "待生成"
INVOICE_STATUS_RUNNING = "生成中"
INVOICE_STATUS_DONE = "生成完成"
INVOICE_STATUS_FAILED = "生成失败"
INVOICE_STATUS_CANCELLED = "已停止"
INVOICE_FINISHED_STATUSES = {INVOICE_STATUS_DONE, INVOICE_STATUS_FAILED, INVOICE_STATUS_CANCELLED}

FAILED_ROW_TAG = "failed"
ERROR_LOG_TAG = "error"
ERROR_LOG_KEYWORDS = ("失败", "错误", "出错", "异常", "Traceback", "Error", "Exception", "failed", "failure")
EMAIL_TASK_PROGRESS_MIN_HEIGHT = 220
EMAIL_TASK_PROGRESS_VISIBLE_ROWS = 8


@dataclass
class RowState:
    entry: ScheduleEntry
    item_id: str
    row_key: str
    status: str = STATUS_PENDING
    message: str = ""


@dataclass
class EmailRowState:
    task: EmailTask
    item_id: str
    row_key: str
    status: str = EMAIL_STATUS_PENDING
    message: str = ""
    reason: str = ""


@dataclass
class InvoiceRowState:
    source: InvoiceDataRow
    item_id: str
    status: str = INVOICE_STATUS_PENDING
    message: str = ""
    reason: str = ""
    output_file: str = ""


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
    def __init__(self, root: Tk | ttk.Frame, *, embedded: bool = False):
        self.root = root
        self.window = root.winfo_toplevel()
        self.embedded = embedded
        if not embedded:
            self.window.title("邮箱登录")
            self.window.geometry("1220x840")
            self.window.minsize(980, 700)
        self.closed = False
        self.store = EmailAccountStore()
        self.login_events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.login_worker: threading.Thread | None = None
        self.email_events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.email_stop_event = threading.Event()
        self.email_worker: threading.Thread | None = None
        self.mail_type = StringVar(value=EMAIL_TYPE_ORDER_CONFIRMATION)
        self.email_subject = StringVar(value=default_email_subject(EMAIL_TYPE_ORDER_CONFIRMATION))
        self.data_file = StringVar()
        self.template_file = StringVar()
        self.attachment_file = StringVar()
        self.template_hint = StringVar(value=placeholder_hint(EMAIL_TYPE_ORDER_CONFIRMATION))
        self.use_region_timezone = BooleanVar(value=True)
        self.saved_account = StringVar()
        self.email = StringVar()
        self.provider = StringVar(value="自动识别")
        self.username = StringVar()
        self.password = StringVar()
        self.smtp_host = StringVar()
        self.smtp_port = IntVar(value=465)
        self.security = StringVar(value="SSL/TLS")
        self.status_text = StringVar(value="请选择或登录邮箱")
        self.email_progress_text = StringVar(value="0/0")
        self.tz = get_timezone("Asia/Shanghai")
        self.email_preview_dir = install_preview_dir()
        self.email_preview_files: set[Path] = set()
        self.email_rows: list[EmailRowState] = []
        self.email_table_columns: list[str] = []
        if not embedded:
            self.window.protocol("WM_DELETE_WINDOW", self._close_window)
        self._build_layout()
        self._refresh_saved_accounts()
        self._poll_login_events()
        self._poll_email_events()
        self._tick_email_countdowns()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1, minsize=EMAIL_TASK_PROGRESS_MIN_HEIGHT)
        outer.rowconfigure(4, weight=0)

        saved_frame = ttk.LabelFrame(outer, text="历史登录邮箱")
        saved_frame.grid(row=0, column=0, sticky="ew")
        saved_frame.columnconfigure(0, weight=1)
        self.saved_box = ttk.Combobox(saved_frame, textvariable=self.saved_account, state="readonly")
        self.saved_box.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self.saved_box.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_account())
        ttk.Button(saved_frame, text="切换登录", command=self.switch_login).grid(row=0, column=1, padx=4, pady=8)
        ttk.Button(saved_frame, text="退出登录", command=self.logout_current).grid(row=0, column=2, padx=4, pady=8)
        ttk.Button(saved_frame, text="删除下拉邮箱", command=self.delete_selected_account).grid(row=0, column=3, padx=4, pady=8)
        if not self.embedded:
            ttk.Button(saved_frame, text="返回", command=self._back).grid(row=0, column=4, padx=(16, 8), pady=8)

        login_frame = ttk.LabelFrame(outer, text="邮箱登录")
        login_frame.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        login_frame.columnconfigure(1, weight=1)
        login_frame.columnconfigure(3, weight=1)

        ttk.Label(login_frame, text="邮箱类型").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.provider_box = ttk.Combobox(
            login_frame,
            textvariable=self.provider,
            state="readonly",
            values=provider_names(),
        )
        self.provider_box.grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        self.provider_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_provider_defaults())

        ttk.Label(login_frame, text="邮箱地址").grid(row=0, column=2, sticky="w", padx=8, pady=8)
        ttk.Entry(login_frame, textvariable=self.email).grid(row=0, column=3, sticky="ew", padx=4, pady=8)

        ttk.Label(login_frame, text="登录账号").grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(login_frame, textvariable=self.username).grid(row=1, column=1, sticky="ew", padx=4, pady=8)

        ttk.Label(login_frame, text="密码/授权码").grid(row=1, column=2, sticky="w", padx=8, pady=8)
        ttk.Entry(login_frame, textvariable=self.password, show="*").grid(row=1, column=3, sticky="ew", padx=4, pady=8)

        ttk.Label(login_frame, text="SMTP服务器").grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(login_frame, textvariable=self.smtp_host).grid(row=2, column=1, sticky="ew", padx=4, pady=8)

        ttk.Label(login_frame, text="端口").grid(row=2, column=2, sticky="w", padx=8, pady=8)
        port_frame = ttk.Frame(login_frame)
        port_frame.grid(row=2, column=3, sticky="ew", padx=4, pady=8)
        ttk.Spinbox(port_frame, from_=1, to=65535, textvariable=self.smtp_port, width=8).pack(side="left")
        ttk.Combobox(
            port_frame,
            textvariable=self.security,
            state="readonly",
            width=12,
            values=security_options(),
        ).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(login_frame)
        actions.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 10))
        self.login_button = ttk.Button(actions, text="登录并保存", command=self.login_and_save)
        self.login_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status_text).pack(side="left", padx=16)

        task_frame = ttk.LabelFrame(outer, text="邮件任务")
        task_frame.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        task_frame.columnconfigure(1, weight=1)
        task_frame.columnconfigure(3, weight=1)

        type_frame = ttk.Frame(task_frame)
        type_frame.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(type_frame, text="邮件类型").pack(side="left", padx=(0, 8))
        for email_type in email_type_names():
            ttk.Radiobutton(
                type_frame,
                text=email_type,
                value=email_type,
                variable=self.mail_type,
                command=self._on_mail_type_changed,
            ).pack(side="left", padx=(0, 16))

        ttk.Label(task_frame, text="邮件主题").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(task_frame, textvariable=self.email_subject).grid(row=1, column=1, columnspan=3, sticky="ew", padx=4, pady=6)

        ttk.Label(task_frame, text="数据文件").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(task_frame, textvariable=self.data_file).grid(row=2, column=1, columnspan=2, sticky="ew", padx=4, pady=6)
        ttk.Button(task_frame, text="选择数据文件（必选）", command=self.choose_email_data_file).grid(row=2, column=3, sticky="e", padx=8, pady=6)

        ttk.Label(task_frame, text="邮件模板").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(task_frame, textvariable=self.template_file).grid(row=3, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(task_frame, text="选择模板", command=self.choose_email_template_file).grid(row=3, column=2, sticky="e", padx=4, pady=6)
        ttk.Button(task_frame, text="清空模板", command=lambda: self.template_file.set("")).grid(row=3, column=3, sticky="e", padx=8, pady=6)

        ttk.Label(task_frame, text="附件").grid(row=4, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(task_frame, textvariable=self.attachment_file).grid(row=4, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(task_frame, text="选择附件", command=self.choose_email_attachment_file).grid(row=4, column=2, sticky="e", padx=4, pady=6)
        ttk.Button(task_frame, text="清空附件", command=lambda: self.attachment_file.set("")).grid(row=4, column=3, sticky="e", padx=8, pady=6)

        ttk.Label(task_frame, textvariable=self.template_hint, foreground="#666666").grid(
            row=5,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=8,
            pady=(4, 6),
        )
        task_actions = ttk.Frame(task_frame)
        task_actions.grid(row=6, column=0, columnspan=4, sticky="ew", padx=8, pady=(4, 10))
        ttk.Checkbutton(task_actions, text="按地区/时区发送", variable=self.use_region_timezone).pack(side="left", padx=(0, 12))
        ttk.Button(task_actions, text="校验并预览", command=self.validate_current_email_task).pack(side="left")
        ttk.Button(task_actions, text="预览邮件", command=self.preview_email_template).pack(side="left", padx=8)
        self.start_email_button = ttk.Button(task_actions, text="开始发送", command=self.start_email_tasks)
        self.start_email_button.pack(side="left")
        self.stop_email_button = ttk.Button(task_actions, text="停止等待", command=self.stop_email_tasks, state="disabled")
        self.stop_email_button.pack(side="left")
        ttk.Button(task_actions, text="导出任务日志", command=self.export_email_progress).pack(side="left", padx=8)
        ttk.Label(task_actions, textvariable=self.email_progress_text).pack(side="right")
        self.email_progress = ttk.Progressbar(task_actions, mode="determinate", length=220)
        self.email_progress.pack(side="right", padx=8)

        table_frame = ttk.LabelFrame(outer, text="邮件任务进度")
        table_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.email_table = ttk.Treeview(table_frame, show="headings", height=EMAIL_TASK_PROGRESS_VISIBLE_ROWS)
        email_y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.email_table.yview)
        email_x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.email_table.xview)
        self.email_table.configure(yscrollcommand=email_y_scroll.set, xscrollcommand=email_x_scroll.set)
        self.email_table.tag_configure(FAILED_ROW_TAG, foreground="#b00020")
        self.email_table.grid(row=0, column=0, sticky="nsew")
        email_y_scroll.grid(row=0, column=1, sticky="ns")
        email_x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(outer, text="运行日志")
        log_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.email_log = scrolledtext.ScrolledText(log_frame, height=10, state="disabled")
        self.email_log.tag_configure(ERROR_LOG_TAG, foreground="#b00020")
        self.email_log.pack(fill="both", expand=True)

        note = (
            "说明：Gmail、QQ、163、Outlook 等通常需要使用“授权码/应用专用密码”，"
            "不是网页登录密码。登录信息会保存在本机用户数据目录。"
        )
        ttk.Label(outer, text=note, foreground="#666666").grid(row=5, column=0, sticky="ew", pady=(10, 0))

    def _on_mail_type_changed(self) -> None:
        email_type = self.mail_type.get()
        known_subjects = {default_email_subject(name) for name in email_type_names()}
        if not self.email_subject.get().strip() or self.email_subject.get().strip() in known_subjects:
            self.email_subject.set(default_email_subject(email_type))
        self.template_hint.set(placeholder_hint(email_type))
        if email_type == EMAIL_TYPE_VAT_INVOICE:
            self._append_email_log("VAT发票邮件：数据文件必选；邮件模板文件和附件PDF文件二选一。")
        elif email_type in {EMAIL_TYPE_ORDER_CONFIRMATION, EMAIL_TYPE_SHIPPING_CONFIRMATION, EMAIL_TYPE_CUSTOM}:
            self._append_email_log(f"{email_type}：数据文件和邮件模板文件都必选，变量格式使用 {{{{变量名}}}}。")

    def choose_email_data_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择邮件数据文件",
            filetypes=(("Data files", "*.csv *.xlsx"), ("CSV files", "*.csv"), ("Excel files", "*.xlsx"), ("All files", "*.*")),
        )
        if filename:
            self.data_file.set(filename)
            self._clear_email_tasks()
            self._append_email_log(f"已选择邮件数据文件：{filename}")

    def choose_email_template_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择邮件模板文件",
            filetypes=(("Template files", "*.html *.htm *.txt *.md"), ("HTML files", "*.html *.htm"), ("Text files", "*.txt *.md"), ("All files", "*.*")),
        )
        if filename:
            self.template_file.set(filename)
            self._append_email_log(f"已选择邮件模板文件：{filename}")

    def choose_email_attachment_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择邮件附件",
            filetypes=(("PDF files", "*.pdf"), ("All files", "*.*")),
        )
        if filename:
            self.attachment_file.set(filename)
            self._append_email_log(f"已选择邮件附件：{filename}")

    def validate_current_email_task(self) -> None:
        result = validate_email_task(
            email_type=self.mail_type.get(),
            data_file=self._optional_path(self.data_file.get()),
            template_file=self._optional_path(self.template_file.get()),
            attachment_file=self._optional_path(self.attachment_file.get()),
        )
        self.status_text.set("邮件任务校验通过" if result.ok else "邮件任务校验失败")
        if result.ok:
            self._append_email_log(f"{self.mail_type.get()} 校验通过")
            try:
                tasks = self._load_email_tasks()
            except Exception as exc:
                self.status_text.set("邮件任务排期失败")
                self._append_email_log(f"邮件任务排期失败：{exc}", level=ERROR_LOG_TAG)
            else:
                self._display_email_tasks(tasks)
                self.status_text.set(f"已生成邮件任务，共 {len(tasks)} 条")
                self._append_email_log(f"已生成邮件任务，共 {len(tasks)} 条")
        for error in result.errors:
            self._append_email_log(error, level=ERROR_LOG_TAG)
        for warning in result.warnings:
            self._append_email_log(f"提示：{warning}")
        if result.placeholders:
            self._append_email_log("模板变量：" + "、".join(result.placeholders))
        if result.preview:
            preview = result.preview.strip()
            if len(preview) > 1200:
                preview = preview[:1200] + "\n...（预览已截断）"
            self._append_email_log("第一行数据预览：\n" + preview)

    def preview_email_template(self) -> None:
        data_file = self._optional_path(self.data_file.get())
        template_file = self._optional_path(self.template_file.get())
        if data_file is None:
            self._append_email_log("预览失败：请选择邮件数据文件。", level=ERROR_LOG_TAG)
            return
        if template_file is None:
            self._append_email_log("预览失败：请选择邮件模板文件。", level=ERROR_LOG_TAG)
            return

        result = validate_email_task(
            email_type=self.mail_type.get(),
            data_file=data_file,
            template_file=template_file,
            attachment_file=None,
        )
        if not result.ok:
            self.status_text.set("邮件预览校验失败")
            for error in result.errors:
                self._append_email_log(error, level=ERROR_LOG_TAG)
            for warning in result.warnings:
                self._append_email_log(f"提示：{warning}")
            return

        try:
            self._cleanup_email_preview_files(include_stale=True)
            preview = build_email_preview_page(
                email_type=self.mail_type.get(),
                data_file=data_file,
                template_file=template_file,
                subject_template=self.email_subject.get() or default_email_subject(self.mail_type.get()),
                output_dir=self.email_preview_dir,
            )
        except Exception as exc:
            self.status_text.set("邮件预览生成失败")
            self._append_email_log(f"邮件预览生成失败：{exc}", level=ERROR_LOG_TAG)
            return

        self.email_preview_files.add(preview.path)
        self.status_text.set(f"邮件预览已生成，共 {preview.count} 条")
        self._append_email_log(f"邮件预览已生成：{preview.path}")
        self._append_email_log("预览页面默认显示第一条数据，可用上一条/下一条切换。")
        self._open_email_preview_file(preview.path)

    def _open_email_preview_file(self, path: Path) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        except Exception as exc:
            self._append_email_log(f"自动打开预览失败：{exc}", level=ERROR_LOG_TAG)
            self._append_email_log(f"请手动打开预览文件：{path}")

    def _cleanup_email_preview_files(self, *, include_stale: bool = False) -> None:
        paths = set(self.email_preview_files)
        if include_stale and self.email_preview_dir.exists():
            paths.update(self.email_preview_dir.glob("email-preview-*.html"))

        for path in sorted(paths):
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                self._append_email_log(f"预览文件清理失败：{path}，{exc}", level=ERROR_LOG_TAG)
            else:
                self.email_preview_files.discard(path)

        try:
            if self.email_preview_dir.exists() and not any(self.email_preview_dir.iterdir()):
                self.email_preview_dir.rmdir()
        except OSError:
            pass

    def _optional_path(self, value: str) -> Path | None:
        value = (value or "").strip()
        return Path(value) if value else None

    def _load_email_tasks(self) -> list[EmailTask]:
        data_file = self._optional_path(self.data_file.get())
        if data_file is None:
            raise ValueError("请选择邮件数据文件。")
        return build_email_tasks(
            data_file,
            default_tz=self.tz,
            use_region_timezone=self.use_region_timezone.get(),
        )

    def start_email_tasks(self) -> None:
        if self.email_worker and self.email_worker.is_alive():
            self._append_email_log("邮件发送任务正在运行中，请稍等")
            return

        account = self.store.active_account()
        if account is None:
            self.status_text.set("请先登录邮箱")
            self._append_email_log("请先登录并保存一个发件邮箱。", level=ERROR_LOG_TAG)
            return

        validation = validate_email_task(
            email_type=self.mail_type.get(),
            data_file=self._optional_path(self.data_file.get()),
            template_file=self._optional_path(self.template_file.get()),
            attachment_file=self._optional_path(self.attachment_file.get()),
        )
        if not validation.ok:
            self.status_text.set("邮件任务校验失败")
            for error in validation.errors:
                self._append_email_log(error, level=ERROR_LOG_TAG)
            for warning in validation.warnings:
                self._append_email_log(f"提示：{warning}")
            return

        try:
            tasks = self._load_email_tasks()
        except Exception as exc:
            self.status_text.set("邮件任务排期失败")
            self._append_email_log(f"邮件任务排期失败：{exc}", level=ERROR_LOG_TAG)
            return

        self._display_email_tasks(tasks)
        self.email_stop_event.clear()
        self.start_email_button.configure(state="disabled")
        self.stop_email_button.configure(state="normal")
        self.status_text.set("邮件任务已启动，等待发送时间")
        self._append_email_log(f"邮件任务启动，共 {len(tasks)} 封，发件邮箱：{account.email}")

        self.email_worker = threading.Thread(
            target=self._run_email_worker,
            kwargs={
                "tasks": tasks,
                "account": account,
                "email_type": self.mail_type.get(),
                "subject_template": self.email_subject.get(),
                "template_file": self._optional_path(self.template_file.get()),
                "attachment_file": self._optional_path(self.attachment_file.get()),
            },
            daemon=True,
        )
        self.email_worker.start()

    def stop_email_tasks(self) -> None:
        self.email_stop_event.set()
        self.status_text.set("正在停止邮件等待，当前发送动作会先结束")
        self._append_email_log("收到停止邮件发送请求")

    def export_email_progress(self) -> None:
        if not self.email_rows or not self.email_table_columns:
            self._append_email_log("当前没有可导出的邮件任务日志，请先校验预览或开始发送。", level=ERROR_LOG_TAG)
            return

        default_name = f"{self.mail_type.get()}-{datetime.now(self.tz).strftime('%Y%m%d-%H%M%S')}.csv"
        filename = filedialog.asksaveasfilename(
            title="导出邮件任务日志",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not filename:
            return

        path = Path(filename)
        try:
            self._write_email_progress_csv(path)
        except Exception as exc:
            self.status_text.set("邮件任务日志导出失败")
            self._append_email_log(f"邮件任务日志导出失败：{exc}", level=ERROR_LOG_TAG)
            return

        self.status_text.set(f"邮件任务日志已导出：{path}")
        self._append_email_log(f"邮件任务日志已导出：{path}")

    def _write_email_progress_csv(self, path: Path) -> None:
        headers, rows = self._email_progress_export_data()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)

    def _email_progress_export_data(self) -> tuple[list[str], list[list[str]]]:
        headers = []
        for column in self.email_table_columns:
            try:
                heading = self.email_table.heading(column, option="text")
            except Exception:
                heading = ""
            headers.append(heading or column)
        return headers, [self._email_row_values(row) for row in self.email_rows]

    def _display_email_tasks(self, tasks: list[EmailTask]) -> None:
        self.email_rows.clear()
        self.email_table.delete(*self.email_table.get_children())
        computed_columns = {"status", "countdown", "scheduled_at", "timezone", "recipient", "message", "reason"}
        raw_columns = [column for column in tasks[0].row.keys() if column not in computed_columns] if tasks else []
        self.email_table_columns = [
            "status",
            "countdown",
            "scheduled_at",
            "timezone",
            "recipient",
            "message",
            "reason",
            *raw_columns,
        ]
        self.email_table.configure(columns=self.email_table_columns)

        headings = {
            "status": "状态",
            "countdown": "预计发送倒计时",
            "scheduled_at": "计划发送时间",
            "timezone": "当地时区",
            "recipient": "收信邮箱",
            "message": "执行信息",
            "reason": "失败原因",
        }
        widths = {
            "status": 90,
            "countdown": 130,
            "scheduled_at": 180,
            "timezone": 170,
            "recipient": 220,
            "message": 220,
            "reason": 280,
            "email": 220,
            "收件邮箱": 220,
            "邮件": 220,
        }
        for column in self.email_table_columns:
            self.email_table.heading(column, text=headings.get(column, column))
            self.email_table.column(column, width=widths.get(column, 140), minwidth=80, stretch=True)

        for task in tasks:
            row = EmailRowState(task=task, item_id="", row_key=task.row_key)
            item_id = self.email_table.insert("", "end", values=self._email_row_values(row), tags=self._email_row_tags(row))
            row.item_id = item_id
            self.email_rows.append(row)
        self._refresh_email_progress()

    def _clear_email_tasks(self) -> None:
        self.email_rows.clear()
        self.email_table_columns = []
        if hasattr(self, "email_table"):
            self.email_table.delete(*self.email_table.get_children())
        if hasattr(self, "email_progress"):
            self.email_progress.configure(maximum=1, value=0)
        self.email_progress_text.set("0/0")

    def _run_email_worker(
        self,
        *,
        tasks: list[EmailTask],
        account,
        email_type: str,
        subject_template: str,
        template_file: Path | None,
        attachment_file: Path | None,
    ) -> None:
        try:
            for task in tasks:
                if self.email_stop_event.is_set():
                    self._emit_email("email_cancelled", task=task, message="用户停止等待")
                    continue

                if task.scheduled_at > datetime.now(task.scheduled_at.tzinfo):
                    self._wait_email_until(task)
                else:
                    self._emit_email("email_waiting", task=task, message="运行时间已到，准备发送")

                if self.email_stop_event.is_set():
                    self._emit_email("email_cancelled", task=task, message="用户停止等待")
                    continue

                self._emit_email("email_sending", task=task, message="正在生成并发送邮件")
                try:
                    message = compose_email_message(
                        account=account,
                        task=task,
                        email_type=email_type,
                        subject_template=subject_template,
                        template_file=template_file,
                        attachment_file=attachment_file,
                    )
                    result = send_email_message(account, message)
                except Exception as exc:
                    self._emit_email("email_failed", task=task, message=self._format_email_exception(exc))
                    continue
                recipients = "、".join(result.accepted_recipients)
                self._emit_email("email_sent", task=task, message=f"SMTP已接收：{recipients}")
        except Exception as exc:
            self._emit_email("email_fatal", message=str(exc), traceback=traceback.format_exc())
        finally:
            self._emit_email("email_worker_done")

    def _wait_email_until(self, task: EmailTask) -> None:
        while not self.email_stop_event.is_set():
            now = datetime.now(task.scheduled_at.tzinfo)
            if now >= task.scheduled_at:
                return
            remaining = max(0, int((task.scheduled_at - now).total_seconds()))
            self._emit_email("email_waiting", task=task, message=f"距离发送 {format_seconds(remaining)}")
            self.email_stop_event.wait(min(1, remaining))

    def _emit_email(self, event: str, **payload) -> None:
        self.email_events.put((event, payload))

    def _poll_email_events(self) -> None:
        if self.closed:
            return
        try:
            while True:
                event, payload = self.email_events.get_nowait()
                self._handle_email_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_email_events)

    def _handle_email_event(self, event: str, payload: dict) -> None:
        task = payload.get("task")
        message = payload.get("message", "")
        recipient = task.recipient if task else ""

        if event == "email_waiting" and task:
            row = self._set_email_row_status(task.row_key, EMAIL_STATUS_WAITING, message)
            self.status_text.set(message)
            if row and row.message != message and row.status == EMAIL_STATUS_WAITING:
                row.message = message
            return

        if event == "email_sending" and task:
            self._set_email_row_status(task.row_key, EMAIL_STATUS_SENDING, message)
            self.status_text.set(f"{recipient} 正在发送")
            self._append_email_log(f"{recipient}: 正在发送邮件")
        elif event == "email_sent" and task:
            self._set_email_row_status(task.row_key, EMAIL_STATUS_SENT, message)
            self.status_text.set(f"{recipient} 发送完成")
            self._append_email_log(f"{recipient}: 发送完成")
        elif event == "email_failed" and task:
            self._set_email_row_status(task.row_key, EMAIL_STATUS_FAILED, "发送失败", reason=message)
            self.status_text.set(f"{recipient} 发送失败")
            self._append_email_log(f"{recipient}: 发送失败，{message}", level=ERROR_LOG_TAG)
        elif event == "email_cancelled" and task:
            self._set_email_row_status(task.row_key, EMAIL_STATUS_CANCELLED, message)
            self._append_email_log(f"{recipient}: {message}")
        elif event == "email_fatal":
            self.status_text.set("邮件任务出错")
            self._append_email_log(f"邮件任务出错：{message}", level=ERROR_LOG_TAG)
            if payload.get("traceback"):
                self._append_email_log(payload["traceback"], level=ERROR_LOG_TAG)
        elif event == "email_worker_done":
            self.start_email_button.configure(state="normal")
            self.stop_email_button.configure(state="disabled")
            if self.status_text.get() != "邮件任务出错":
                self.status_text.set("邮件任务结束")
            self._append_email_log("邮件任务结束")

        self._refresh_email_progress()

    def _set_email_row_status(
        self,
        row_key: str,
        status: str,
        message: str,
        *,
        reason: str = "",
    ) -> EmailRowState | None:
        row = self._find_email_row(row_key)
        if row is None:
            return None
        row.status = status
        row.message = message
        if reason:
            row.reason = reason
        self.email_table.item(row.item_id, values=self._email_row_values(row), tags=self._email_row_tags(row))
        return row

    def _find_email_row(self, row_key: str) -> EmailRowState | None:
        for row in self.email_rows:
            if row.row_key == row_key:
                return row
        return None

    def _tick_email_countdowns(self) -> None:
        if self.closed:
            return
        for row in self.email_rows:
            if row.status in {EMAIL_STATUS_PENDING, EMAIL_STATUS_WAITING, EMAIL_STATUS_SENDING}:
                self.email_table.item(row.item_id, values=self._email_row_values(row), tags=self._email_row_tags(row))
        self.root.after(1000, self._tick_email_countdowns)

    def _email_row_values(self, row: EmailRowState) -> list[str]:
        task = row.task
        computed = {
            "status": row.status,
            "countdown": self._email_countdown_text(row),
            "scheduled_at": self._format_scheduled_at(task.scheduled_at),
            "timezone": self._format_timezone(task.scheduled_at),
            "recipient": task.recipient,
            "message": row.message,
            "reason": row.reason,
        }
        return [computed.get(column, task.row.get(column, "")) for column in self.email_table_columns]

    def _email_row_tags(self, row: EmailRowState) -> tuple[str, ...]:
        if row.status == EMAIL_STATUS_FAILED:
            return (FAILED_ROW_TAG,)
        return ()

    def _email_countdown_text(self, row: EmailRowState) -> str:
        if row.status in EMAIL_FINISHED_STATUSES:
            return "-"
        if row.status == EMAIL_STATUS_SENDING:
            return "正在发送"
        remaining = int((row.task.scheduled_at - datetime.now(row.task.scheduled_at.tzinfo)).total_seconds())
        if remaining <= 0:
            return "到点"
        return format_seconds(remaining)

    def _refresh_email_progress(self) -> None:
        total = len(self.email_rows)
        finished = sum(row.status in EMAIL_FINISHED_STATUSES for row in self.email_rows)
        self.email_progress.configure(maximum=max(1, total), value=finished)
        self.email_progress_text.set(f"{finished}/{total}")

    def _format_email_exception(self, exc: Exception) -> str:
        text = str(exc).strip()
        return text or type(exc).__name__

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

    def _refresh_saved_accounts(self, selected_email: str | None = None, *, auto_select: bool = True) -> None:
        accounts, active_email = self.store.load()
        values = [account.email for account in accounts]
        self.saved_box.configure(values=values)
        chosen = selected_email or active_email or (values[0] if auto_select and values else "")
        self.saved_account.set(chosen if chosen in values else "")
        if self.saved_account.get():
            self._load_selected_account()

    def _load_selected_account(self) -> None:
        account = self.store.get(self.saved_account.get())
        if account is None:
            return
        self.email.set(account.email)
        self.provider.set(account.provider)
        self.username.set(account.username)
        self.password.set(account.password)
        self.smtp_host.set(account.smtp_host)
        self.smtp_port.set(account.smtp_port)
        self.security.set(account.security)
        self.status_text.set(f"已加载：{account.email}")

    def _apply_provider_defaults(self) -> None:
        try:
            settings = resolve_provider_settings(self.email.get(), self.provider.get())
        except EmailLoginError:
            if self.provider.get() == "自定义":
                self.smtp_host.set("")
                self.smtp_port.set(465)
                self.security.set("SSL/TLS")
            return
        if settings.smtp_host:
            self.smtp_host.set(settings.smtp_host)
        self.smtp_port.set(settings.smtp_port)
        self.security.set(settings.security)

    def login_and_save(self) -> None:
        if self.login_worker and self.login_worker.is_alive():
            self._append_email_log("邮箱登录正在进行中，请稍等")
            return
        try:
            account = make_login_info(
                email=self.email.get(),
                provider=self.provider.get(),
                smtp_host=self.smtp_host.get(),
                smtp_port=int(self.smtp_port.get()),
                security=self.security.get(),
                username=self.username.get(),
                password=self.password.get(),
            )
        except Exception as exc:
            self.status_text.set("邮箱登录信息不完整")
            self._append_email_log(str(exc), level=ERROR_LOG_TAG)
            return

        self.login_button.configure(state="disabled")
        self.status_text.set("正在登录邮箱...")
        self._append_email_log(f"正在登录邮箱：{account.email}")
        self.login_worker = threading.Thread(target=self._login_worker, args=(account,), daemon=True)
        self.login_worker.start()

    def _login_worker(self, account) -> None:
        try:
            verify_smtp_login(account)
            self.store.upsert(account, set_active=True)
            self.login_events.put(("login_success", {"email": account.email}))
        except Exception as exc:
            self.login_events.put(("login_failed", {"message": str(exc)}))

    def _poll_login_events(self) -> None:
        if self.closed:
            return
        try:
            while True:
                event, payload = self.login_events.get_nowait()
                self._handle_login_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_login_events)

    def _handle_login_event(self, event: str, payload: dict) -> None:
        self.login_button.configure(state="normal")
        if event == "login_success":
            email = payload["email"]
            self.status_text.set(f"邮箱登录成功：{email}")
            self._append_email_log(f"邮箱登录成功并已保存：{email}")
            self._refresh_saved_accounts(email)
        elif event == "login_failed":
            self.status_text.set("邮箱登录失败")
            self._append_email_log(payload.get("message", "邮箱登录失败"), level=ERROR_LOG_TAG)

    def switch_login(self) -> None:
        email = self.saved_account.get()
        if not email:
            self._append_email_log("请先在下拉列表选择历史邮箱", level=ERROR_LOG_TAG)
            return
        try:
            self.store.set_active(email)
        except EmailLoginError as exc:
            self._append_email_log(str(exc), level=ERROR_LOG_TAG)
            return
        self._load_selected_account()
        self._append_email_log(f"已切换登录邮箱：{email}")

    def logout_current(self) -> None:
        email = self.saved_account.get()
        if not email:
            self._append_email_log("当前没有可退出的邮箱", level=ERROR_LOG_TAG)
            return
        self.store.delete(email)
        self._clear_form()
        self._refresh_saved_accounts(auto_select=False)
        self.status_text.set(f"已退出并删除登录信息：{email}")
        self._append_email_log(f"已退出并删除登录信息：{email}")

    def delete_selected_account(self) -> None:
        email = self.saved_account.get()
        if not email:
            self._append_email_log("请先在下拉列表选择要删除的邮箱", level=ERROR_LOG_TAG)
            return
        self.store.delete(email)
        if self.email.get().strip().lower() == email.lower():
            self._clear_form()
        self._refresh_saved_accounts(auto_select=False)
        self.status_text.set(f"已删除邮箱登录信息：{email}")
        self._append_email_log(f"已删除邮箱登录信息：{email}")

    def _clear_form(self) -> None:
        self.saved_account.set("")
        self.email.set("")
        self.provider.set("自动识别")
        self.username.set("")
        self.password.set("")
        self.smtp_host.set("")
        self.smtp_port.set(465)
        self.security.set("SSL/TLS")

    def _append_email_log(self, message: str, *, level: str | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        tag = ERROR_LOG_TAG if level == ERROR_LOG_TAG or self._is_error_log_message(message) else None
        self.email_log.configure(state="normal")
        if tag:
            self.email_log.insert("end", f"[{timestamp}] {message}\n", tag)
        else:
            self.email_log.insert("end", f"[{timestamp}] {message}\n")
        self.email_log.see("end")
        self.email_log.configure(state="disabled")

    def _is_error_log_message(self, message: str) -> bool:
        lowered = (message or "").casefold()
        return any(keyword.casefold() in lowered for keyword in ERROR_LOG_KEYWORDS)

    def _back(self) -> None:
        self.shutdown()
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        clear_root(self.root)
        ModeSelectionApp(self.root)

    def shutdown(self) -> None:
        self.closed = True
        self.email_stop_event.set()
        self._cleanup_email_preview_files(include_stale=True)

    def _close_window(self) -> None:
        self.shutdown()
        self.window.destroy()


class InvoiceApp:
    def __init__(self, root: Tk | ttk.Frame, *, embedded: bool = False):
        self.root = root
        self.window = root.winfo_toplevel()
        self.embedded = embedded
        if not embedded:
            self.window.title("批量生成发票")
            self.window.geometry("1280x840")
            self.window.minsize(980, 700)
            self.window.protocol("WM_DELETE_WINDOW", self._close_window)

        self.data_file = StringVar()
        self.output_dir = StringVar()
        self.invoice_output_format = StringVar(value=INVOICE_OUTPUT_FORMAT_LABELS[INVOICE_OUTPUT_FORMAT_PDF])
        self.status_text = StringVar(value="请选择发票数据文件")
        self.progress_text = StringVar(value="0/0")
        self.invoice_events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.invoice_worker: threading.Thread | None = None
        self.invoice_stop_event = threading.Event()
        self.invoice_rows: list[InvoiceRowState] = []
        self.invoice_rows_by_index: dict[int, InvoiceRowState] = {}
        self.invoice_table_columns: list[str] = []
        self.company_names: list[str] = []
        self.template_vars: dict[str, StringVar] = {}
        self.preview_files: set[Path] = set()
        self.preview_window: Toplevel | None = None
        self.preview_status = StringVar()
        self.preview_index = 0
        self.tz = get_timezone("Asia/Shanghai")
        self._build_layout()
        self._poll_invoice_events()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1, minsize=260)
        outer.rowconfigure(3, weight=0)

        data_frame = ttk.LabelFrame(outer, text="发票数据")
        data_frame.grid(row=0, column=0, sticky="ew")
        data_frame.columnconfigure(1, weight=1)
        data_frame.columnconfigure(4, weight=1)
        ttk.Label(data_frame, text="数据文件").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(data_frame, textvariable=self.data_file).grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        ttk.Button(data_frame, text="选择数据文件", command=self.choose_invoice_data_file).grid(row=0, column=2, padx=8, pady=8)
        ttk.Button(data_frame, text="识别公司", command=self.load_invoice_data_file).grid(row=0, column=3, padx=4, pady=8)
        ttk.Label(data_frame, text="输出目录").grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(data_frame, textvariable=self.output_dir).grid(row=1, column=1, columnspan=3, sticky="ew", padx=4, pady=8)
        ttk.Button(data_frame, text="选择输出目录", command=self.choose_invoice_output_dir).grid(row=1, column=4, padx=8, pady=8)
        ttk.Label(data_frame, text="导出格式").grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ttk.Combobox(
            data_frame,
            textvariable=self.invoice_output_format,
            state="readonly",
            width=18,
            values=invoice_output_format_labels(),
        ).grid(row=2, column=1, sticky="w", padx=4, pady=8)
        ttk.Label(data_frame, textvariable=self.status_text).grid(row=3, column=0, columnspan=5, sticky="ew", padx=8, pady=(0, 8))

        self.template_frame = ttk.LabelFrame(outer, text="发票模板（按 company_name 自动生成入口）")
        self.template_frame.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        self.template_frame.columnconfigure(1, weight=1)
        ttk.Label(
            self.template_frame,
            text="请选择数据文件并点击“识别公司”，系统会按公司名称生成对应模板上传入口。",
            foreground="#666666",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=10)

        table_frame = ttk.LabelFrame(outer, text="发票任务进度")
        table_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.invoice_table = ttk.Treeview(table_frame, show="headings", height=10)
        invoice_y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.invoice_table.yview)
        invoice_x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.invoice_table.xview)
        self.invoice_table.configure(yscrollcommand=invoice_y_scroll.set, xscrollcommand=invoice_x_scroll.set)
        self.invoice_table.tag_configure(FAILED_ROW_TAG, foreground="#b00020")
        self.invoice_table.grid(row=0, column=0, sticky="nsew")
        invoice_y_scroll.grid(row=0, column=1, sticky="ns")
        invoice_x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="预览发票", command=self.preview_invoice).pack(side="left")
        self.start_invoice_button = ttk.Button(actions, text="批量生成发票", command=self.start_invoice_generation)
        self.start_invoice_button.pack(side="left", padx=8)
        self.stop_invoice_button = ttk.Button(actions, text="停止等待", command=self.stop_invoice_generation, state="disabled")
        self.stop_invoice_button.pack(side="left")
        ttk.Label(actions, textvariable=self.progress_text).pack(side="right")
        self.invoice_progress = ttk.Progressbar(actions, mode="determinate", length=220)
        self.invoice_progress.pack(side="right", padx=8)

        log_frame = ttk.LabelFrame(outer, text="运行日志")
        log_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.invoice_log = scrolledtext.ScrolledText(log_frame, height=9, state="disabled")
        self.invoice_log.tag_configure(ERROR_LOG_TAG, foreground="#b00020")
        self.invoice_log.pack(fill="both", expand=True)

    def choose_invoice_data_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择发票数据文件",
            filetypes=(("Data files", "*.csv *.xlsx"), ("CSV files", "*.csv"), ("Excel files", "*.xlsx"), ("All files", "*.*")),
        )
        if not filename:
            return
        self.data_file.set(filename)
        if not self.output_dir.get().strip():
            self.output_dir.set(str(log_dir().parent / "generated_invoices"))
        self.load_invoice_data_file()

    def choose_invoice_output_dir(self) -> None:
        dirname = filedialog.askdirectory(title="选择发票输出目录")
        if dirname:
            self.output_dir.set(dirname)

    def load_invoice_data_file(self) -> None:
        data_path = self._optional_path(self.data_file.get())
        if data_path is None:
            self._append_invoice_log("请选择发票数据文件。", level=ERROR_LOG_TAG)
            self.status_text.set("请选择发票数据文件")
            return
        try:
            rows = load_invoice_rows(data_path)
        except Exception as exc:
            self.status_text.set("发票数据读取失败")
            self._append_invoice_log(f"发票数据读取失败：{exc}", level=ERROR_LOG_TAG)
            self._clear_invoice_rows()
            self._render_company_template_inputs([])
            return

        self.company_names = unique_companies(rows)
        self._display_invoice_rows(rows)
        self._render_company_template_inputs(self.company_names)
        self.status_text.set(f"已识别 {len(rows)} 条发票数据、{len(self.company_names)} 家公司")
        self._append_invoice_log(f"已识别 {len(rows)} 条发票数据，发现公司：{', '.join(self.company_names)}")

    def _render_company_template_inputs(self, companies: list[str]) -> None:
        for child in self.template_frame.winfo_children():
            child.destroy()
        if not companies:
            ttk.Label(
                self.template_frame,
                text="请选择数据文件并点击“识别公司”，系统会按公司名称生成对应模板上传入口。",
                foreground="#666666",
            ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=10)
            return

        self.template_frame.columnconfigure(1, weight=1)
        for row_index, company in enumerate(companies):
            company_key = normalize_company_key(company)
            template_var = self.template_vars.setdefault(company_key, StringVar())
            ttk.Label(self.template_frame, text=company).grid(row=row_index, column=0, sticky="w", padx=8, pady=6)
            ttk.Entry(self.template_frame, textvariable=template_var).grid(row=row_index, column=1, sticky="ew", padx=4, pady=6)
            ttk.Button(
                self.template_frame,
                text="选择模板",
                command=lambda current_company=company: self.choose_invoice_template(current_company),
            ).grid(row=row_index, column=2, sticky="e", padx=8, pady=6)

    def choose_invoice_template(self, company: str) -> None:
        filename = filedialog.askopenfilename(
            title=f"选择 {company} 的发票模板",
            filetypes=(
                ("Invoice templates", "*.pdf *.html *.htm"),
                ("PDF files", "*.pdf"),
                ("HTML files", "*.html *.htm"),
                ("All files", "*.*"),
            ),
        )
        if not filename:
            return
        company_key = normalize_company_key(company)
        self.template_vars.setdefault(company_key, StringVar()).set(filename)
        try:
            analysis = analyze_invoice_template(Path(filename))
        except Exception as exc:
            self._append_invoice_log(f"{company} 模板读取失败：{exc}", level=ERROR_LOG_TAG)
            return
        if analysis.style == "html":
            fields = "、".join(analysis.field_names[:8])
            suffix = "..." if len(analysis.field_names) > 8 else ""
            self._append_invoice_log(
                f"{company} HTML模板已选择：{analysis.path.name}，检测到 {len(analysis.field_names)} 个变量：{fields}{suffix}"
            )
        elif analysis.field_names:
            fields = "、".join(analysis.field_names[:8])
            suffix = "..." if len(analysis.field_names) > 8 else ""
            self._append_invoice_log(
                f"{company} 模板已选择：{analysis.path.name}，检测到 {len(analysis.field_names)} 个表单变量：{fields}{suffix}"
            )
        else:
            self._append_invoice_log(
                f"{company} 模板已选择：{analysis.path.name}，未检测到表单变量，将使用固定坐标覆盖；页数：{analysis.page_count}"
            )

    def preview_invoice(self) -> None:
        if not self.invoice_rows:
            self.load_invoice_data_file()
        if not self.invoice_rows:
            return
        self.preview_index = min(self.preview_index, len(self.invoice_rows) - 1)
        self._open_invoice_preview_window()
        self._render_invoice_preview(self.preview_index)

    def _open_invoice_preview_window(self) -> None:
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.lift()
            return
        self.preview_window = Toplevel(self.window)
        self.preview_window.title("发票预览")
        self.preview_window.geometry("620x180")
        self.preview_window.minsize(520, 160)
        self.preview_window.protocol("WM_DELETE_WINDOW", self._close_invoice_preview_window)
        frame = ttk.Frame(self.preview_window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.preview_status, wraplength=560).pack(fill="x")
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(18, 0))
        ttk.Button(button_frame, text="上一条", command=lambda: self._switch_invoice_preview(-1)).pack(side="left")
        ttk.Button(button_frame, text="下一条", command=lambda: self._switch_invoice_preview(1)).pack(side="left", padx=8)
        ttk.Button(button_frame, text="重新打开预览", command=self._open_current_preview_file).pack(side="left")
        ttk.Button(button_frame, text="关闭并清理", command=self._close_invoice_preview_window).pack(side="right")

    def _switch_invoice_preview(self, delta: int) -> None:
        if not self.invoice_rows:
            return
        self.preview_index = (self.preview_index + delta) % len(self.invoice_rows)
        self._render_invoice_preview(self.preview_index)

    def _render_invoice_preview(self, index: int) -> None:
        row_state = self.invoice_rows[index]
        template_path = self._template_path_for_company(row_state.source.company_name)
        if template_path is None:
            message = f"{row_state.source.company_name} 未选择发票模板，无法预览。"
            self.preview_status.set(message)
            self._append_invoice_log(message, level=ERROR_LOG_TAG)
            return

        preview_dir = invoice_preview_dir()
        timestamp = datetime.now(self.tz).strftime("%Y%m%d-%H%M%S-%f")
        try:
            output_format = invoice_output_format_from_label(self.invoice_output_format.get())
        except Exception as exc:
            message = f"发票预览生成失败：{exc}"
            self.preview_status.set(message)
            self._append_invoice_log(message, level=ERROR_LOG_TAG)
            return
        preview_path = preview_dir / f"invoice-preview-{timestamp}{invoice_output_extension(output_format)}"
        try:
            result = generate_invoice_file(
                template_path=template_path,
                row=row_state.source,
                output_path=preview_path,
                output_format=output_format,
            )
        except Exception as exc:
            message = f"发票预览生成失败：{exc}"
            self.preview_status.set(message)
            self._append_invoice_log(message, level=ERROR_LOG_TAG)
            return

        self.preview_files.add(result.output_path)
        invoice_number = row_state.source.values.get("vat_invoice_number", "") or f"第 {row_state.source.row_index} 行"
        self.preview_status.set(f"正在预览 {index + 1}/{len(self.invoice_rows)}：{row_state.source.company_name}，{invoice_number}\n{result.output_path}")
        self._append_invoice_log(f"发票预览已生成：{result.output_path}")
        self._open_file(result.output_path)

    def _open_current_preview_file(self) -> None:
        if not self.preview_files:
            self._render_invoice_preview(self.preview_index)
            return
        latest = max(self.preview_files, key=lambda path: path.stat().st_mtime if path.exists() else 0)
        self._open_file(latest)

    def _close_invoice_preview_window(self) -> None:
        self._cleanup_invoice_preview_files()
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None

    def start_invoice_generation(self) -> None:
        if self.invoice_worker and self.invoice_worker.is_alive():
            self._append_invoice_log("发票生成任务正在运行中，请稍等")
            return
        if not self.invoice_rows:
            self.load_invoice_data_file()
        if not self.invoice_rows:
            return

        output_dir = self._optional_path(self.output_dir.get()) or (log_dir().parent / "generated_invoices")
        try:
            output_format = invoice_output_format_from_label(self.invoice_output_format.get())
        except Exception as exc:
            self.status_text.set("发票导出格式错误")
            self._append_invoice_log(f"发票导出格式错误：{exc}", level=ERROR_LOG_TAG)
            return
        template_map = self._template_map()
        missing_companies = [
            company
            for company in self.company_names
            if normalize_company_key(company) not in template_map
        ]
        if missing_companies:
            self.status_text.set("发票模板未选择完整")
            self._append_invoice_log("以下公司未选择发票模板：" + "、".join(missing_companies), level=ERROR_LOG_TAG)
            return

        self.invoice_stop_event.clear()
        self.start_invoice_button.configure(state="disabled")
        self.stop_invoice_button.configure(state="normal")
        self.status_text.set("发票生成任务已启动")
        self._append_invoice_log(f"发票生成任务启动，共 {len(self.invoice_rows)} 条，格式：{output_format}，输出目录：{output_dir}")
        self.invoice_worker = threading.Thread(
            target=self._run_invoice_worker,
            kwargs={"output_dir": output_dir, "template_map": template_map, "output_format": output_format},
            daemon=True,
        )
        self.invoice_worker.start()

    def stop_invoice_generation(self) -> None:
        self.invoice_stop_event.set()
        self.status_text.set("正在停止发票生成")
        self._append_invoice_log("收到停止发票生成请求")

    def _run_invoice_worker(self, *, output_dir: Path, template_map: dict[str, Path], output_format: str) -> None:
        try:
            for row_state in self.invoice_rows:
                source = row_state.source
                if self.invoice_stop_event.is_set():
                    self._emit_invoice("invoice_cancelled", row_index=source.row_index, message="用户停止生成")
                    continue
                template_path = template_map[normalize_company_key(source.company_name)]
                output_path = output_invoice_path(output_dir, source, output_format=output_format)
                self._emit_invoice(
                    "invoice_running",
                    row_index=source.row_index,
                    message=f"正在生成 {source.company_name} 发票（{output_format}）",
                )
                try:
                    result = generate_invoice_file(
                        template_path=template_path,
                        row=source,
                        output_path=output_path,
                        output_format=output_format,
                    )
                except Exception as exc:
                    self._emit_invoice("invoice_failed", row_index=source.row_index, reason=str(exc))
                else:
                    self._emit_invoice(
                        "invoice_done",
                        row_index=source.row_index,
                        output_file=str(result.output_path),
                        message="发票已生成",
                    )
        finally:
            self._emit_invoice("invoice_worker_done")

    def _emit_invoice(self, event: str, **payload) -> None:
        self.invoice_events.put((event, payload))

    def _poll_invoice_events(self) -> None:
        try:
            while True:
                event, payload = self.invoice_events.get_nowait()
                self._handle_invoice_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(250, self._poll_invoice_events)

    def _handle_invoice_event(self, event: str, payload: dict) -> None:
        row_index = payload.get("row_index")
        row = self.invoice_rows_by_index.get(row_index) if row_index is not None else None
        if event == "invoice_running" and row:
            row.status = INVOICE_STATUS_RUNNING
            row.message = payload.get("message", "正在生成")
            self._append_invoice_log(f"第 {row.source.row_index} 行：{row.message}")
            self._refresh_invoice_row(row)
        elif event == "invoice_done" and row:
            row.status = INVOICE_STATUS_DONE
            row.message = payload.get("message", "发票已生成")
            row.output_file = payload.get("output_file", "")
            self._append_invoice_log(f"第 {row.source.row_index} 行：发票已生成 {row.output_file}")
            self._refresh_invoice_row(row)
        elif event == "invoice_failed" and row:
            row.status = INVOICE_STATUS_FAILED
            row.reason = payload.get("reason", "未知错误")
            row.message = "发票生成失败"
            self._append_invoice_log(f"第 {row.source.row_index} 行：发票生成失败，{row.reason}", level=ERROR_LOG_TAG)
            self._refresh_invoice_row(row)
        elif event == "invoice_cancelled" and row:
            row.status = INVOICE_STATUS_CANCELLED
            row.message = payload.get("message", "已停止")
            self._append_invoice_log(f"第 {row.source.row_index} 行：已停止", level=ERROR_LOG_TAG)
            self._refresh_invoice_row(row)
        elif event == "invoice_worker_done":
            self.start_invoice_button.configure(state="normal")
            self.stop_invoice_button.configure(state="disabled")
            self.status_text.set("发票生成任务结束")
            self._append_invoice_log("发票生成任务结束")
        self._refresh_invoice_progress()

    def _display_invoice_rows(self, rows: list[InvoiceDataRow]) -> None:
        self.invoice_rows = [InvoiceRowState(source=row, item_id="") for row in rows]
        self.invoice_rows_by_index = {row.source.row_index: row for row in self.invoice_rows}
        raw_columns = sorted({key for row in rows for key in row.values.keys()})
        computed_columns = {"status", "company_name", "row_index", "invoice_number", "message", "reason", "output_file"}
        self.invoice_table_columns = [
            "status",
            "company_name",
            "row_index",
            "invoice_number",
            "message",
            "reason",
            "output_file",
            *[column for column in raw_columns if column not in computed_columns],
        ]
        self.invoice_table.delete(*self.invoice_table.get_children())
        self.invoice_table.configure(columns=self.invoice_table_columns)
        headings = {
            "status": "状态",
            "company_name": "公司名称",
            "row_index": "数据行",
            "invoice_number": "发票号",
            "message": "执行信息",
            "reason": "失败原因",
            "output_file": "输出文件",
        }
        widths = {
            "status": 90,
            "company_name": 260,
            "row_index": 80,
            "invoice_number": 150,
            "message": 180,
            "reason": 280,
            "output_file": 320,
        }
        for column in self.invoice_table_columns:
            self.invoice_table.heading(column, text=headings.get(column, column))
            self.invoice_table.column(column, width=widths.get(column, 140), minwidth=80, stretch=True)
        for row in self.invoice_rows:
            item_id = self.invoice_table.insert("", "end", values=self._invoice_row_values(row), tags=self._invoice_row_tags(row))
            row.item_id = item_id
        self._refresh_invoice_progress()

    def _clear_invoice_rows(self) -> None:
        self.invoice_rows.clear()
        self.invoice_rows_by_index.clear()
        self.company_names = []
        self.invoice_table_columns = []
        if hasattr(self, "invoice_table"):
            self.invoice_table.delete(*self.invoice_table.get_children())
        if hasattr(self, "invoice_progress"):
            self.invoice_progress.configure(maximum=1, value=0)
        self.progress_text.set("0/0")

    def _refresh_invoice_row(self, row: InvoiceRowState) -> None:
        if row.item_id:
            self.invoice_table.item(row.item_id, values=self._invoice_row_values(row), tags=self._invoice_row_tags(row))

    def _invoice_row_values(self, row: InvoiceRowState) -> list[str]:
        values = {
            "status": row.status,
            "company_name": row.source.company_name,
            "row_index": str(row.source.row_index),
            "invoice_number": row.source.values.get("vat_invoice_number", ""),
            "message": row.message,
            "reason": row.reason,
            "output_file": row.output_file,
            **row.source.values,
        }
        return [values.get(column, "") for column in self.invoice_table_columns]

    def _invoice_row_tags(self, row: InvoiceRowState) -> tuple[str, ...]:
        return (FAILED_ROW_TAG,) if row.status in {INVOICE_STATUS_FAILED, INVOICE_STATUS_CANCELLED} else ()

    def _refresh_invoice_progress(self) -> None:
        total = len(self.invoice_rows)
        finished = sum(1 for row in self.invoice_rows if row.status in INVOICE_FINISHED_STATUSES)
        if hasattr(self, "invoice_progress"):
            self.invoice_progress.configure(maximum=max(1, total), value=finished)
        self.progress_text.set(f"{finished}/{total}")

    def _template_map(self) -> dict[str, Path]:
        template_map: dict[str, Path] = {}
        for company in self.company_names:
            company_key = normalize_company_key(company)
            template_var = self.template_vars.get(company_key)
            template_path = self._optional_path(template_var.get()) if template_var is not None else None
            if template_path and template_path.exists():
                template_map[company_key] = template_path
        return template_map

    def _template_path_for_company(self, company: str) -> Path | None:
        company_key = normalize_company_key(company)
        template_var = self.template_vars.get(company_key)
        if template_var is None:
            return None
        template_path = self._optional_path(template_var.get())
        if template_path and template_path.exists():
            return template_path
        return None

    def _open_file(self, path: Path) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        except Exception as exc:
            self._append_invoice_log(f"自动打开文件失败：{exc}", level=ERROR_LOG_TAG)
            self._append_invoice_log(f"请手动打开文件：{path}")

    def _cleanup_invoice_preview_files(self) -> None:
        paths = set(self.preview_files)
        preview_dir = invoice_preview_dir()
        if preview_dir.exists():
            paths.update(preview_dir.glob("invoice-preview-*.*"))
        for path in sorted(paths):
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                self._append_invoice_log(f"发票预览清理失败：{path}，{exc}", level=ERROR_LOG_TAG)
            else:
                self.preview_files.discard(path)
        try:
            if preview_dir.exists() and not any(preview_dir.iterdir()):
                preview_dir.rmdir()
        except OSError:
            pass

    def _optional_path(self, value: str) -> Path | None:
        value = (value or "").strip()
        return Path(value) if value else None

    def _append_invoice_log(self, message: str, *, level: str | None = None) -> None:
        timestamp = datetime.now(self.tz).strftime("%H:%M:%S")
        tag = ERROR_LOG_TAG if level == ERROR_LOG_TAG or self._is_error_log_message(message) else None
        self.invoice_log.configure(state="normal")
        if tag:
            self.invoice_log.insert("end", f"[{timestamp}] {message}\n", tag)
        else:
            self.invoice_log.insert("end", f"[{timestamp}] {message}\n")
        self.invoice_log.see("end")
        self.invoice_log.configure(state="disabled")

    def _is_error_log_message(self, message: str) -> bool:
        lowered = (message or "").casefold()
        return any(keyword.casefold() in lowered for keyword in ERROR_LOG_KEYWORDS)

    def shutdown(self) -> None:
        self.invoice_stop_event.set()
        self._cleanup_invoice_preview_files()

    def _close_window(self) -> None:
        self.shutdown()
        self.window.destroy()


class OrderBotApp:
    def __init__(self, root: Tk | ttk.Frame, *, embedded: bool = False):
        self.root = root
        self.window = root.winfo_toplevel()
        self.embedded = embedded
        if not embedded:
            self.window.title("自动下单机器人")
            self.window.geometry("1280x760")
            self.window.minsize(980, 620)

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

    def shutdown(self) -> None:
        self.stop_event.set()

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


class MainTabbedApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("自动下单机器人")
        self.root.geometry("1280x860")
        self.root.minsize(980, 700)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        self.order_frame = ttk.Frame(self.notebook)
        self.email_frame = ttk.Frame(self.notebook)
        self.invoice_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.order_frame, text="去下单")
        self.notebook.add(self.email_frame, text="发邮件")
        self.notebook.add(self.invoice_frame, text="生成发票")

        self.order_app = OrderBotApp(self.order_frame, embedded=True)
        self.email_app = EmailApp(self.email_frame, embedded=True)
        self.invoice_app = InvoiceApp(self.invoice_frame, embedded=True)
        self.root.protocol("WM_DELETE_WINDOW", self._close_window)

    def _close_window(self) -> None:
        for app in (self.order_app, self.email_app, self.invoice_app):
            try:
                app.shutdown()
            except Exception:
                pass
        self.root.destroy()


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
    MainTabbedApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
