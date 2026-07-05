from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from order_bot.browser_client import ADD_TO_BAG_SELECTORS, PLACE_ORDER_SELECTORS, BrowserOrderClient, normalize_country_text


class BrowserClientConfigTests(unittest.TestCase):
    def test_failure_and_country_fallback_defaults_are_off(self):
        client = BrowserOrderClient()

        self.assertFalse(client.keep_open_on_failure)
        self.assertFalse(client.allow_detected_country_on_mismatch)

    def test_country_text_normalization(self):
        self.assertEqual(normalize_country_text("  United   Kingdom "), "united kingdom")
        self.assertEqual(normalize_country_text("HONG KONG"), "hong kong")

    def test_target_closed_error_detection(self):
        client = BrowserOrderClient()

        self.assertTrue(
            client._is_target_closed_error(
                RuntimeError("Mouse.wheel: Target page, context or browser has been closed")
            )
        )
        self.assertFalse(client._is_target_closed_error(RuntimeError("some other playwright error")))

    def test_packaged_app_uses_current_directory_browser_cache(self):
        client = BrowserOrderClient()

        with tempfile.TemporaryDirectory() as temp_dir:
            current_dir = Path(temp_dir)
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {}, clear=False),
                patch("pathlib.Path.cwd", return_value=current_dir),
            ):
                os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

                client._configure_packaged_playwright()

                browser_path = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
                self.assertEqual(browser_path, current_dir / "playwright-browsers")

    def test_add_to_bag_selectors_cover_foenix_product_button(self):
        self.assertIn("button[data-qa='productsection-btn-addtobag']", ADD_TO_BAG_SELECTORS)
        self.assertIn(
            ".block-product__button-wrapper button.block-product__button--primary[data-qa='productsection-btn-addtobag']",
            ADD_TO_BAG_SELECTORS,
        )

    def test_place_order_selectors_cover_foenix_checkout_button(self):
        self.assertIn("button[type='submit']:has-text('Place an order')", PLACE_ORDER_SELECTORS)
        self.assertIn("button:has-text('Place an order')", PLACE_ORDER_SELECTORS)


if __name__ == "__main__":
    unittest.main()
