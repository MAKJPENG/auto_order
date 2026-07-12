from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from order_bot.email_templates import (
    EMAIL_TYPE_CUSTOM,
    EMAIL_TYPE_ORDER_CONFIRMATION,
    EMAIL_TYPE_SHIPPING_CONFIRMATION,
    EMAIL_TYPE_VAT_INVOICE,
    EMAIL_TYPE_SPECS,
    extract_placeholders,
    render_template,
    validate_email_task,
)


class EmailTemplatesTests(unittest.TestCase):
    def test_extract_placeholders_uses_double_brace_format(self):
        self.assertEqual(extract_placeholders("Hello {{客户姓名}}, order {{订单号}}"), ["客户姓名", "订单号"])

    def test_order_confirmation_requires_fixed_required_columns_and_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,订单号,商品描述,数量\nbuyer@example.com,2026-07-05 10:00,ORD-1,Bracelet,2\n",
                encoding="utf-8-sig",
            )
            template_file = root / "template.html"
            template_file.write_text(
                "{{订单号}} {{商品描述}} {{数量}}\n",
                encoding="utf-8",
            )

            result = validate_email_task(
                email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                data_file=data_file,
                template_file=template_file,
                attachment_file=None,
            )

        self.assertFalse(result.ok)
        self.assertIn("数据文件缺少必填列：含VAT总价", result.errors)
        self.assertIn("模板缺少必填变量：{{含VAT总价}}", result.errors)

    def test_shipping_confirmation_validates_required_tracking_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,订单号,物流单号\nbuyer@example.com,2026-07-05 10:00,ORD-1,TRACK-1\n",
                encoding="utf-8-sig",
            )
            template_file = root / "template.txt"
            template_file.write_text("订单 {{订单号}} 物流单号 {{物流单号}}", encoding="utf-8")

            result = validate_email_task(
                email_type=EMAIL_TYPE_SHIPPING_CONFIRMATION,
                data_file=data_file,
                template_file=template_file,
                attachment_file=None,
            )

        self.assertTrue(result.ok)
        self.assertIn("TRACK-1", result.preview)

    def test_vat_invoice_requires_template_or_pdf_attachment_exclusively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text("email,run_at\nbuyer@example.com,2026-07-05 10:00\n", encoding="utf-8-sig")
            template_file = root / "template.txt"
            template_file.write_text("VAT", encoding="utf-8")
            pdf_file = root / "invoice.pdf"
            pdf_file.write_bytes(b"%PDF-1.4\n")

            both_result = validate_email_task(
                email_type=EMAIL_TYPE_VAT_INVOICE,
                data_file=data_file,
                template_file=template_file,
                attachment_file=pdf_file,
            )
            pdf_result = validate_email_task(
                email_type=EMAIL_TYPE_VAT_INVOICE,
                data_file=data_file,
                template_file=None,
                attachment_file=pdf_file,
            )

        self.assertFalse(both_result.ok)
        self.assertIn("VAT发票邮件的邮件模板文件和附件PDF文件只能二选一，不能同时上传。", both_result.errors)
        self.assertTrue(pdf_result.ok)

    def test_vat_invoice_pdf_mode_still_requires_data_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_file = root / "invoice.pdf"
            pdf_file.write_bytes(b"%PDF-1.4\n")

            result = validate_email_task(
                email_type=EMAIL_TYPE_VAT_INVOICE,
                data_file=None,
                template_file=None,
                attachment_file=pdf_file,
            )

        self.assertFalse(result.ok)
        self.assertIn("所有邮件类型都必须上传数据文件。", result.errors)

    def test_non_vat_email_requires_data_file_and_template_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,订单号,商品描述,数量,含VAT总价\n"
                "buyer@example.com,2026-07-05 10:00,ORD-1,Bracelet,2,£100\n",
                encoding="utf-8-sig",
            )

            result = validate_email_task(
                email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                data_file=data_file,
                template_file=None,
                attachment_file=None,
            )

        self.assertFalse(result.ok)
        self.assertIn("订单确认邮件 必须上传邮件模板文件。", result.errors)

    def test_custom_template_warns_missing_data_column_but_keeps_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text("email,run_at,客户姓名\nbuyer@example.com,2026-07-05 10:00,Alice\n", encoding="utf-8-sig")
            template_file = root / "template.txt"
            template_file.write_text("Hi {{客户姓名}}, code {{优惠码}}", encoding="utf-8")

            result = validate_email_task(
                email_type=EMAIL_TYPE_CUSTOM,
                data_file=data_file,
                template_file=template_file,
                attachment_file=None,
            )

        self.assertTrue(result.ok)
        self.assertIn("Alice", result.preview)
        self.assertIn("{{优惠码}}", result.preview)
        self.assertIn("自定义变量未找到数据列，发送时会标记失败：优惠码", result.warnings)

    def test_render_template_replaces_aliases_and_keeps_missing_values(self):
        rendered = render_template(
            "订单 {{Order Number}} 缺失 {{不存在}}",
            {"order_id": "ORD-9"},
            EMAIL_TYPE_SPECS[EMAIL_TYPE_ORDER_CONFIRMATION],
        )

        self.assertEqual(rendered, "订单 ORD-9 缺失 {{不存在}}")


if __name__ == "__main__":
    unittest.main()
