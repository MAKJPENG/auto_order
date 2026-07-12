from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from order_bot.email_accounts import EmailLoginInfo, SECURITY_SSL
from order_bot.email_tasks import build_email_tasks, compose_email_message
from order_bot.email_templates import EMAIL_TYPE_ORDER_CONFIRMATION
from order_bot.time_utils import get_timezone


class EmailTasksTests(unittest.TestCase):
    def test_build_email_tasks_uses_region_timezone_when_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "data.csv"
            data_file.write_text(
                "收件邮箱,运行时间,地区,客户姓名\n"
                "buyer@example.com,2026-07-05 10:00,United Kingdom,Alice\n",
                encoding="utf-8-sig",
            )

            tasks = build_email_tasks(data_file, default_tz=get_timezone("Asia/Shanghai"))

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].recipient, "buyer@example.com")
        self.assertEqual(tasks[0].scheduled_at.hour, 10)
        self.assertEqual(tasks[0].timezone_name, "Europe/London")
        self.assertEqual(tasks[0].scheduled_at.strftime("%z"), "+0100")

    def test_build_email_tasks_uses_default_timezone_without_region(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "data.csv"
            data_file.write_text(
                "email,run_at,客户姓名\nbuyer@example.com,2026-07-05 10:00,Alice\n",
                encoding="utf-8-sig",
            )

            tasks = build_email_tasks(data_file, default_tz=get_timezone("Asia/Shanghai"))

        self.assertEqual(tasks[0].timezone_name, "Asia/Shanghai")
        self.assertEqual(tasks[0].scheduled_at.strftime("%z"), "+0800")

    def test_compose_email_message_replaces_subject_and_body_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            template_file = Path(temp_dir) / "template.txt"
            template_file.write_text("Hi {{客户姓名}}, order {{订单号}}", encoding="utf-8")
            data_file = Path(temp_dir) / "data.csv"
            data_file.write_text(
                "email,run_at,客户姓名,订单号,商品描述,数量,含VAT总价\n"
                "buyer@example.com,2026-07-05 10:00,Alice,ORD-1,Bracelet,1,£100\n",
                encoding="utf-8-sig",
            )
            task = build_email_tasks(data_file, default_tz=get_timezone("Asia/Shanghai"))[0]
            account = EmailLoginInfo(
                email="sender@example.com",
                provider="自定义",
                smtp_host="smtp.example.com",
                smtp_port=465,
                security=SECURITY_SSL,
                username="sender@example.com",
                password="secret",
            )

            message = compose_email_message(
                account=account,
                task=task,
                email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                subject_template="订单 {{订单号}}",
                template_file=template_file,
                attachment_file=None,
            )

        self.assertEqual(message["To"], "buyer@example.com")
        self.assertEqual(message["Subject"], "订单 ORD-1")
        self.assertIn("Hi Alice, order ORD-1", message.get_content())

    def test_compose_email_message_replaces_text_attachment_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template_file = root / "template.txt"
            template_file.write_text("Body {{name}}", encoding="utf-8")
            attachment_file = root / "attachment.txt"
            attachment_file.write_text("Attachment for {{name}} / {{order_id}}", encoding="utf-8")
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,name,order_id,product_description,quantity,total_price\n"
                "buyer@example.com,2026-07-05 10:00,Alice,ORD-1,Bracelet,1,£100\n",
                encoding="utf-8-sig",
            )
            task = build_email_tasks(data_file, default_tz=get_timezone("Asia/Shanghai"))[0]
            account = EmailLoginInfo(
                email="sender@example.com",
                provider="自定义",
                smtp_host="smtp.example.com",
                smtp_port=465,
                security=SECURITY_SSL,
                username="sender@example.com",
                password="secret",
            )

            message = compose_email_message(
                account=account,
                task=task,
                email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                subject_template="订单 {{order_id}}",
                template_file=template_file,
                attachment_file=attachment_file,
            )

        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertIn("Attachment for Alice / ORD-1", attachments[0].get_content())

    def test_custom_email_requires_run_time_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "data.csv"
            data_file.write_text("email,客户姓名\nbuyer@example.com,Alice\n", encoding="utf-8-sig")

            with self.assertRaisesRegex(ValueError, "运行时间列"):
                build_email_tasks(data_file, default_tz=get_timezone("Asia/Shanghai"))


if __name__ == "__main__":
    unittest.main()
