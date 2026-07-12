from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from order_bot.email_preview import build_email_preview_page
from order_bot.email_templates import EMAIL_TYPE_ORDER_CONFIRMATION


class EmailPreviewTests(unittest.TestCase):
    def test_build_email_preview_page_contains_all_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,name,order_id,product_description,quantity,total_price\n"
                "alice@example.com,2026-07-05 10:00,Alice,ORD-1,Bracelet,1,£100\n"
                "bob@example.com,2026-07-05 11:00,Bob,ORD-2,Ring,2,£200\n",
                encoding="utf-8-sig",
            )
            template_file = root / "template.html"
            template_file.write_text("<h1>{{name}}</h1><p>{{order_id}}</p>", encoding="utf-8")

            result = build_email_preview_page(
                email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                data_file=data_file,
                template_file=template_file,
                subject_template="订单 {{order_id}}",
                output_dir=root / "previews",
            )

            html = result.path.read_text(encoding="utf-8")

        self.assertEqual(result.count, 2)
        self.assertIn("alice@example.com", html)
        self.assertIn("bob@example.com", html)
        self.assertIn("ArrowLeft", html)
        self.assertIn("ArrowRight", html)
        self.assertIn("previewFrame", html)

    def test_build_email_preview_page_rejects_missing_template_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_file = root / "data.csv"
            data_file.write_text(
                "email,run_at,name,order_id,product_description,quantity,total_price\n"
                "alice@example.com,2026-07-05 10:00,Alice,ORD-1,Bracelet,1,£100\n",
                encoding="utf-8-sig",
            )
            template_file = root / "template.html"
            template_file.write_text("<h1>{{missing_column}}</h1>", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing_column"):
                build_email_preview_page(
                    email_type=EMAIL_TYPE_ORDER_CONFIRMATION,
                    data_file=data_file,
                    template_file=template_file,
                    subject_template="订单 {{order_id}}",
                    output_dir=root / "previews",
                )


if __name__ == "__main__":
    unittest.main()
