from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from order_bot.csv_loader import load_orders
from order_bot.time_utils import get_timezone


class CsvLoaderTests(unittest.TestCase):
    def test_payment_method_defaults_to_bank_transfer(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,country,address_line,city,postal_code,notes",
                "order-1,,buyer@example.com,https://example.com/product,1,Test Buyer,United Kingdom,1 Test Street,Birmingham,B1 1BA,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"))

        self.assertEqual(orders[0].payment_method, "bank_transfer")
        self.assertEqual(orders[0].raw["payment_method"], "bank_transfer")
        self.assertEqual(orders[0].phone, "")

    def test_phone_is_optional_but_loaded_when_present(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,phone,country,address_line,city,postal_code,notes",
                "order-1,,buyer@example.com,https://example.com/product,1,Test Buyer,+441234567890,United Kingdom,1 Test Street,Birmingham,B1 1BA,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"))

        self.assertEqual(orders[0].phone, "+441234567890")
        self.assertEqual(orders[0].raw["phone"], "+441234567890")

    def test_product_url_accepts_comma_separated_urls(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,country,address_line,city,postal_code,notes",
                'order-1,,buyer@example.com,"https://example.com/product-a, https://example.com/product-b",1,Test Buyer,United Kingdom,1 Test Street,Birmingham,B1 1BA,',
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"))

        self.assertEqual(
            orders[0].product_urls,
            ["https://example.com/product-a", "https://example.com/product-b"],
        )

    def test_run_at_uses_country_timezone_when_enabled(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,country,address_line,city,postal_code,notes",
                "order-1,2026-01-05 09:30,buyer@example.com,https://example.com/product,1,Test Buyer,United Kingdom,1 Test Street,Birmingham,B1 1BA,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"), use_country_timezone=True)

        self.assertEqual(orders[0].time_zone, "Europe/London")
        self.assertEqual(orders[0].run_at.hour, 9)
        self.assertEqual(orders[0].run_at.minute, 30)

    def test_london_run_at_uses_summer_time_offset(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,country,address_line,city,postal_code,notes",
                "order-1,2026-07-04 16:45,buyer@example.com,https://example.com/product,1,Test Buyer,United Kingdom,1 Test Street,Birmingham,B1 1BA,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"), use_country_timezone=True)

        self.assertEqual(orders[0].time_zone, "Europe/London")
        self.assertEqual(orders[0].run_at.utcoffset(), timedelta(hours=1))
        self.assertEqual(orders[0].run_at.isoformat(sep=" ", timespec="seconds"), "2026-07-04 16:45:00+01:00")

    def test_timezone_column_overrides_country_mapping(self):
        content = "\n".join(
            [
                "order_id,run_at,email,product_url,quantity,full_name,country,timezone,address_line,city,postal_code,notes",
                "order-1,2026-01-05 09:30,buyer@example.com,https://example.com/product,1,Test Buyer,United States,America/Los_Angeles,1 Test Street,Los Angeles,90001,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "orders.csv"
            csv_path.write_text(content, encoding="utf-8")

            orders = load_orders(csv_path, get_timezone("Asia/Shanghai"), use_country_timezone=True)

        self.assertEqual(orders[0].time_zone, "America/Los_Angeles")


if __name__ == "__main__":
    unittest.main()
