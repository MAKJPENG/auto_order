from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import skipIf

from order_bot.invoice_generator import (
    INVOICE_OUTPUT_FORMAT_HTML,
    INVOICE_OUTPUT_FORMAT_PDF,
    InvoiceDataRow,
    generate_invoice_file,
    load_invoice_rows,
    normalize_company_key,
    normalize_invoice_header,
    output_invoice_path,
    safe_filename,
    unique_companies,
)


def _missing_pdf_dependencies() -> bool:
    try:
        import pypdf  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        return True
    return False


class InvoiceGeneratorTests(unittest.TestCase):
    def test_normalize_invoice_headers_to_snake_case_variables(self):
        self.assertEqual(normalize_invoice_header("VAT Invoice Number"), "vat_invoice_number")
        self.assertEqual(normalize_invoice_header("Date of Supply"), "date_of_supply")
        self.assertEqual(normalize_invoice_header("Unit Price"), "unit_price")
        self.assertEqual(normalize_invoice_header("公司名称"), "company_name")

    def test_load_invoice_rows_uses_supplier_as_company_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.csv"
            path.write_text(
                "VAT Invoice Number,Supplier,SKU,Description\n"
                "INV-1,TAGVENUE LIMITED,SKU-1,Venue booking\n",
                encoding="utf-8-sig",
            )

            rows = load_invoice_rows(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_name, "TAGVENUE LIMITED")
        self.assertEqual(rows[0].values["vat_invoice_number"], "INV-1")
        self.assertEqual(rows[0].values["company_name"], "TAGVENUE LIMITED")

    def test_unique_companies_keeps_original_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.csv"
            path.write_text(
                "company_name,vat_invoice_number\n"
                "A LTD,INV-1\n"
                "B LTD,INV-2\n"
                "A Ltd,INV-3\n",
                encoding="utf-8-sig",
            )

            rows = load_invoice_rows(path)

        self.assertEqual(unique_companies(rows), ["A LTD", "B LTD"])

    def test_output_invoice_path_uses_safe_company_and_invoice_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.csv"
            path.write_text(
                "company_name,vat_invoice_number\n"
                "A/B LTD,INV:1\n",
                encoding="utf-8-sig",
            )
            row = load_invoice_rows(path)[0]

            output_path = output_invoice_path(Path(temp_dir), row, output_format=INVOICE_OUTPUT_FORMAT_HTML)

        self.assertEqual(output_path.parent.name, "A-B LTD")
        self.assertEqual(output_path.name, "A-B LTD-INV-1.html")

    def test_generate_invoice_file_from_html_template_replaces_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "template.html"
            output = root / "TAGVENUE LIMITED" / "TAGVENUE LIMITED-INV-HTML-001.html"
            template.write_text(
                "<html><body>{{company_name}} / {{vat_invoice_number}} / {{gross_amount}}</body></html>",
                encoding="utf-8",
            )
            row = InvoiceDataRow(
                row_index=2,
                company_name="TAGVENUE LIMITED",
                values={
                    "company_name": "TAGVENUE LIMITED",
                    "vat_invoice_number": "INV-HTML-001",
                    "gross_amount": "120.00",
                },
            )

            result = generate_invoice_file(
                template_path=template,
                row=row,
                output_path=output,
                output_format=INVOICE_OUTPUT_FORMAT_HTML,
            )
            rendered = output.read_text(encoding="utf-8")

        self.assertEqual(result.output_path, output)
        self.assertIn("TAGVENUE LIMITED", rendered)
        self.assertIn("INV-HTML-001", rendered)
        self.assertIn("120.00", rendered)

    def test_safe_filename_never_returns_empty_value(self):
        self.assertTrue(safe_filename("   "))
        self.assertEqual(normalize_company_key("A/B LTD"), "abltd")

    @skipIf(_missing_pdf_dependencies(), "PDF dependencies are not installed")
    def test_generate_invoice_pdf_fills_acroform_fields_by_variable_name(self):
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "template.pdf"
            output = root / "filled.pdf"
            pdf_canvas = canvas.Canvas(str(template), pagesize=(300, 180))
            pdf_canvas.drawString(24, 145, "Invoice")
            acroform = getattr(pdf_canvas, "acroform", None) or getattr(pdf_canvas, "acroForm")
            acroform.textfield(
                name="vat_invoice_number",
                x=24,
                y=105,
                width=160,
                height=20,
                borderWidth=1,
                forceBorder=True,
            )
            acroform.textfield(
                name="{{gross_amount}}",
                x=24,
                y=70,
                width=160,
                height=20,
                borderWidth=1,
                forceBorder=True,
            )
            pdf_canvas.save()

            row = InvoiceDataRow(
                row_index=2,
                company_name="TAGVENUE LIMITED",
                values={
                    "company_name": "TAGVENUE LIMITED",
                    "vat_invoice_number": "INV-FORM-001",
                    "gross_amount": "120.00",
                },
            )
            generate_invoice_file(
                template_path=template,
                row=row,
                output_path=output,
                output_format=INVOICE_OUTPUT_FORMAT_PDF,
            )
            reader = PdfReader(str(output))
            text = reader.pages[0].extract_text()

        self.assertIn("INV-FORM-001", text)
        self.assertIn("120.00", text)
        self.assertFalse(reader.get_fields())


if __name__ == "__main__":
    unittest.main()
