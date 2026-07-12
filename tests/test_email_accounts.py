from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from order_bot.email_accounts import (
    EmailAccountStore,
    EmailLoginError,
    make_login_info,
    resolve_provider_settings,
)


class EmailAccountsTests(unittest.TestCase):
    def test_auto_provider_resolves_common_email_domain(self):
        provider = resolve_provider_settings("buyer@gmail.com", "自动识别")

        self.assertEqual(provider.name, "Gmail")
        self.assertEqual(provider.smtp_host, "smtp.gmail.com")

    def test_make_login_info_uses_email_as_default_username(self):
        account = make_login_info(
            email="Buyer@QQ.com",
            provider="自动识别",
            smtp_host="",
            smtp_port=0,
            security="",
            username="",
            password="auth-code",
        )

        self.assertEqual(account.email, "buyer@qq.com")
        self.assertEqual(account.username, "buyer@qq.com")
        self.assertEqual(account.provider, "QQ邮箱")
        self.assertEqual(account.smtp_host, "smtp.qq.com")

    def test_unknown_auto_provider_requires_custom_smtp(self):
        with self.assertRaises(EmailLoginError):
            resolve_provider_settings("buyer@example-company.test", "自动识别")

    def test_store_keeps_history_and_switches_active_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = EmailAccountStore(Path(temp_dir) / "email_accounts.json")
            first = make_login_info(
                email="first@gmail.com",
                provider="Gmail",
                smtp_host="",
                smtp_port=0,
                security="",
                username="",
                password="first-secret",
            )
            second = make_login_info(
                email="second@qq.com",
                provider="QQ邮箱",
                smtp_host="",
                smtp_port=0,
                security="",
                username="",
                password="second-secret",
            )

            store.upsert(first)
            store.upsert(second)
            store.set_active(first.email)

            accounts, active_email = store.load()

        self.assertEqual([account.email for account in accounts], ["first@gmail.com", "second@qq.com"])
        self.assertEqual(active_email, "first@gmail.com")
        self.assertEqual(accounts[0].password, "first-secret")

    def test_store_delete_removes_login_info(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "email_accounts.json"
            store = EmailAccountStore(path)
            account = make_login_info(
                email="delete@gmail.com",
                provider="Gmail",
                smtp_host="",
                smtp_port=0,
                security="",
                username="",
                password="delete-secret",
            )

            store.upsert(account)
            self.assertNotIn("delete-secret", path.read_text(encoding="utf-8"))
            store.delete(account.email)
            accounts, active_email = store.load()

        self.assertEqual(accounts, [])
        self.assertEqual(active_email, "")


if __name__ == "__main__":
    unittest.main()
