from __future__ import annotations

import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from .email_accounts import EmailLoginInfo, SECURITY_SSL
from .email_templates import (
    EMAIL_TYPE_CUSTOM,
    EMAIL_TYPE_ORDER_CONFIRMATION,
    EMAIL_TYPE_SHIPPING_CONFIRMATION,
    EMAIL_TYPE_SPECS,
    EMAIL_TYPE_VAT_INVOICE,
    RECIPIENT_ALIASES,
    REGION_ALIASES,
    RUN_AT_ALIASES,
    TIMEZONE_ALIASES,
    find_header,
    load_data_file,
    read_template,
    render_template,
    value_for_aliases,
)
from .time_utils import parse_datetime, resolve_order_timezone, timezone_label


TEXT_ATTACHMENT_SUFFIXES = {".txt", ".html", ".htm", ".md", ".csv", ".json", ".xml"}

DEFAULT_EMAIL_SUBJECTS = {
    EMAIL_TYPE_ORDER_CONFIRMATION: "订单确认邮件",
    EMAIL_TYPE_SHIPPING_CONFIRMATION: "物流邮件",
    EMAIL_TYPE_VAT_INVOICE: "VAT发票邮件",
    EMAIL_TYPE_CUSTOM: "自定义邮件",
}


@dataclass(frozen=True)
class EmailTask:
    row_index: int
    row_key: str
    row: dict[str, str]
    recipient: str
    scheduled_at: datetime
    timezone_name: str
    source: str = "run_at"


def default_email_subject(email_type: str) -> str:
    return DEFAULT_EMAIL_SUBJECTS.get(email_type, "邮件通知")


def build_email_tasks(
    data_file: Path,
    *,
    default_tz,
    use_region_timezone: bool = True,
) -> list[EmailTask]:
    data = load_data_file(data_file)
    if not data.headers:
        raise ValueError("数据文件没有表头。")
    if not data.rows:
        raise ValueError("数据文件没有可发送的数据行。")
    if find_header(data.headers, RECIPIENT_ALIASES) is None:
        raise ValueError("数据文件必须包含收件邮箱列：email / 邮箱 / 收件邮箱。")
    if find_header(data.headers, RUN_AT_ALIASES) is None:
        raise ValueError("数据文件必须包含运行时间列：run_at / 运行时间 / 发送时间。")

    tasks: list[EmailTask] = []
    for index, row in enumerate(data.rows):
        line_number = index + 2
        recipient = (value_for_aliases(row, RECIPIENT_ALIASES) or "").strip()
        if not recipient:
            raise ValueError(f"第 {line_number} 行缺少收件邮箱。")

        run_at_text = (value_for_aliases(row, RUN_AT_ALIASES) or "").strip()
        if not run_at_text:
            raise ValueError(f"第 {line_number} 行缺少运行时间。")

        tz = default_tz
        if use_region_timezone:
            region = (value_for_aliases(row, REGION_ALIASES) or "").strip()
            timezone_name = (value_for_aliases(row, TIMEZONE_ALIASES) or "").strip()
            if region or timezone_name:
                try:
                    tz = resolve_order_timezone(
                        country=region,
                        country_code="",
                        timezone_name=timezone_name,
                        default_tz=default_tz,
                    )
                except ValueError as exc:
                    raise ValueError(f"第 {line_number} 行地区/时区不支持：{exc}") from exc

        scheduled_at = parse_email_datetime(run_at_text, tz)
        tasks.append(
            EmailTask(
                row_index=index,
                row_key=f"email-row-{index}",
                row=row,
                recipient=recipient,
                scheduled_at=scheduled_at,
                timezone_name=timezone_label(scheduled_at.tzinfo),
            )
        )
    return sorted(tasks, key=lambda task: task.scheduled_at)


def parse_email_datetime(value: str, tz) -> datetime:
    try:
        parsed = parse_datetime(value, tz)
    except ValueError:
        parsed = _parse_excel_serial_datetime(value, tz)
        if parsed is None:
            raise
    if parsed is None:
        raise ValueError("运行时间不能为空。")
    return parsed


def compose_email_message(
    *,
    account: EmailLoginInfo,
    task: EmailTask,
    email_type: str,
    subject_template: str,
    template_file: Path | None,
    attachment_file: Path | None,
) -> EmailMessage:
    spec = EMAIL_TYPE_SPECS[email_type]
    subject = render_template(subject_template or default_email_subject(email_type), task.row, spec).strip()
    subject = subject or default_email_subject(email_type)

    body = "请查收附件。"
    is_html = False
    if template_file:
        body = render_template(read_template(template_file), task.row, spec)
        is_html = template_file.suffix.lower() in {".html", ".htm"}

    message = EmailMessage()
    message["From"] = account.email
    message["To"] = task.recipient
    message["Subject"] = subject
    if is_html:
        message.set_content("请使用支持 HTML 的邮箱客户端查看邮件内容。")
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body)

    if attachment_file:
        _attach_file(message, attachment_file, task.row, spec)
    return message


def send_email_message(account: EmailLoginInfo, message: EmailMessage, *, timeout: float = 30) -> None:
    context = ssl.create_default_context()
    if account.security == SECURITY_SSL:
        with smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=timeout, context=context) as smtp:
            smtp.login(account.username, account.password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=timeout) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(account.username, account.password)
        smtp.send_message(message)


def _attach_file(message: EmailMessage, path: Path, row: dict[str, str], spec) -> None:
    if not path.exists():
        raise FileNotFoundError(f"附件不存在：{path}")
    mime_type, _encoding = mimetypes.guess_type(path.name)
    maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
    if path.suffix.lower() in TEXT_ATTACHMENT_SUFFIXES:
        rendered = render_template(read_template(path), row, spec).encode("utf-8")
        message.add_attachment(
            rendered,
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
        return
    message.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def _parse_excel_serial_datetime(value: str, tz) -> datetime | None:
    try:
        serial = float(value)
    except ValueError:
        return None
    if serial <= 0 or serial > 100000:
        return None
    base = datetime(1899, 12, 30)
    parsed = base + timedelta(days=serial)
    return parsed.replace(tzinfo=tz)
