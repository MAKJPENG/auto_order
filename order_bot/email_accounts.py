from __future__ import annotations

import base64
import json
import smtplib
import ssl
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

from .paths import user_data_dir


SECURITY_SSL = "SSL/TLS"
SECURITY_STARTTLS = "STARTTLS"


@dataclass(frozen=True)
class EmailProvider:
    name: str
    smtp_host: str
    smtp_port: int
    security: str
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailLoginInfo:
    email: str
    provider: str
    smtp_host: str
    smtp_port: int
    security: str
    username: str
    password: str
    last_login_at: str = ""


class EmailLoginError(RuntimeError):
    pass


EMAIL_PROVIDERS: tuple[EmailProvider, ...] = (
    EmailProvider("自动识别", "", 465, SECURITY_SSL),
    EmailProvider("Gmail", "smtp.gmail.com", 465, SECURITY_SSL, ("gmail.com", "googlemail.com")),
    EmailProvider("Outlook/Hotmail", "smtp.office365.com", 587, SECURITY_STARTTLS, ("outlook.com", "hotmail.com", "live.com", "msn.com")),
    EmailProvider("Yahoo", "smtp.mail.yahoo.com", 465, SECURITY_SSL, ("yahoo.com", "yahoo.co.uk", "ymail.com")),
    EmailProvider("QQ邮箱", "smtp.qq.com", 465, SECURITY_SSL, ("qq.com", "vip.qq.com")),
    EmailProvider("163邮箱", "smtp.163.com", 465, SECURITY_SSL, ("163.com",)),
    EmailProvider("126邮箱", "smtp.126.com", 465, SECURITY_SSL, ("126.com",)),
    EmailProvider("新浪邮箱", "smtp.sina.com", 465, SECURITY_SSL, ("sina.com", "sina.cn")),
    EmailProvider("iCloud", "smtp.mail.me.com", 587, SECURITY_STARTTLS, ("icloud.com", "me.com", "mac.com")),
    EmailProvider("Zoho", "smtp.zoho.com", 465, SECURITY_SSL, ("zoho.com",)),
    EmailProvider("自定义", "", 465, SECURITY_SSL),
)


def provider_names() -> list[str]:
    return [provider.name for provider in EMAIL_PROVIDERS]


def security_options() -> tuple[str, str]:
    return (SECURITY_SSL, SECURITY_STARTTLS)


def resolve_provider_settings(email: str, provider_name: str) -> EmailProvider:
    normalized_provider = provider_name or "自动识别"
    if normalized_provider not in provider_names():
        raise EmailLoginError(f"不支持的邮箱类型：{normalized_provider}")
    if normalized_provider not in {"自动识别", "自定义"}:
        return _provider_by_name(normalized_provider)
    if normalized_provider == "自定义":
        return _provider_by_name("自定义")

    domain = _email_domain(email)
    for provider in EMAIL_PROVIDERS:
        if domain in provider.domains:
            return provider
    raise EmailLoginError("无法自动识别邮箱服务商，请选择“自定义”并填写 SMTP 信息。")


def make_login_info(
    *,
    email: str,
    provider: str,
    smtp_host: str,
    smtp_port: int,
    security: str,
    username: str,
    password: str,
) -> EmailLoginInfo:
    email = normalize_email(email)
    username = (username or email).strip()
    if not username:
        raise EmailLoginError("请输入邮箱账号。")
    if not password:
        raise EmailLoginError("请输入邮箱密码或授权码。")

    settings = resolve_provider_settings(email, provider)
    smtp_host = (smtp_host or settings.smtp_host).strip()
    smtp_port = int(smtp_port or settings.smtp_port)
    security = security or settings.security
    if security not in security_options():
        raise EmailLoginError(f"不支持的加密方式：{security}")
    if not smtp_host:
        raise EmailLoginError("请填写 SMTP 服务器。")
    if smtp_port <= 0:
        raise EmailLoginError("SMTP 端口必须大于 0。")

    return EmailLoginInfo(
        email=email,
        provider=settings.name if provider == "自动识别" else provider,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        security=security,
        username=username,
        password=password,
        last_login_at=datetime.now().isoformat(timespec="seconds"),
    )


def verify_smtp_login(account: EmailLoginInfo, *, timeout: float = 15) -> None:
    context = ssl.create_default_context()
    try:
        if account.security == SECURITY_SSL:
            with smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=timeout, context=context) as smtp:
                smtp.login(account.username, account.password)
        else:
            with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(account.username, account.password)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailLoginError("邮箱登录失败：账号、密码或授权码不正确。") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailLoginError(f"邮箱登录失败：{exc}") from exc


class EmailAccountStore:
    def __init__(self, path: Path | None = None):
        self.path = path or user_data_dir() / "email_accounts.json"

    def load(self) -> tuple[list[EmailLoginInfo], str]:
        if not self.path.exists():
            return [], ""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [], ""

        accounts = []
        for item in payload.get("accounts", []):
            try:
                accounts.append(_account_from_json(item))
            except (KeyError, TypeError, ValueError):
                continue
        active_email = payload.get("active_email", "")
        if active_email and all(account.email != active_email for account in accounts):
            active_email = ""
        return accounts, active_email

    def list_accounts(self) -> list[EmailLoginInfo]:
        accounts, _active = self.load()
        return accounts

    def active_account(self) -> EmailLoginInfo | None:
        accounts, active_email = self.load()
        for account in accounts:
            if account.email == active_email:
                return account
        return accounts[0] if accounts else None

    def get(self, email: str) -> EmailLoginInfo | None:
        email = normalize_email(email)
        for account in self.list_accounts():
            if account.email == email:
                return account
        return None

    def upsert(self, account: EmailLoginInfo, *, set_active: bool = True) -> None:
        accounts, active_email = self.load()
        updated = [existing for existing in accounts if existing.email != account.email]
        updated.append(account)
        updated.sort(key=lambda item: item.email.lower())
        self._save(updated, account.email if set_active else active_email)

    def set_active(self, email: str) -> None:
        email = normalize_email(email)
        accounts, _active_email = self.load()
        if all(account.email != email for account in accounts):
            raise EmailLoginError(f"没有保存过这个邮箱：{email}")
        self._save(accounts, email)

    def delete(self, email: str) -> None:
        email = normalize_email(email)
        accounts, active_email = self.load()
        updated = [account for account in accounts if account.email != email]
        next_active = "" if active_email == email else active_email
        if next_active and all(account.email != next_active for account in updated):
            next_active = ""
        self._save(updated, next_active)

    def _save(self, accounts: list[EmailLoginInfo], active_email: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_email": active_email,
            "accounts": [_account_to_json(account) for account in accounts],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_email(email: str) -> str:
    parsed = parseaddr((email or "").strip())[1].lower()
    if not parsed or "@" not in parsed:
        raise EmailLoginError("请输入有效的邮箱地址。")
    return parsed


def _email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[1]


def _provider_by_name(name: str) -> EmailProvider:
    for provider in EMAIL_PROVIDERS:
        if provider.name == name:
            return provider
    raise EmailLoginError(f"不支持的邮箱类型：{name}")


def _account_to_json(account: EmailLoginInfo) -> dict[str, object]:
    payload = asdict(account)
    payload["password"] = _encode_secret(account.password)
    return payload


def _account_from_json(payload: dict[str, object]) -> EmailLoginInfo:
    return EmailLoginInfo(
        email=str(payload["email"]),
        provider=str(payload["provider"]),
        smtp_host=str(payload["smtp_host"]),
        smtp_port=int(payload["smtp_port"]),
        security=str(payload["security"]),
        username=str(payload["username"]),
        password=_decode_secret(str(payload["password"])),
        last_login_at=str(payload.get("last_login_at", "")),
    )


def _encode_secret(value: str) -> str:
    return "b64:" + base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def _decode_secret(value: str) -> str:
    if not value.startswith("b64:"):
        return value
    return base64.urlsafe_b64decode(value[4:].encode("ascii")).decode("utf-8")


def account_without_password(account: EmailLoginInfo) -> EmailLoginInfo:
    return replace(account, password="")
