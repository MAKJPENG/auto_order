from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
