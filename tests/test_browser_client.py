from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from order_bot.browser_client import (
    ADD_TO_BAG_SELECTORS,
    CART_CHECKOUT_SELECTORS,
    CART_TOGGLE_SELECTORS,
    PLACE_ORDER_SELECTORS,
    VIEW_CART_SELECTORS,
    BrowserOrderClient,
    country_name_matches,
    normalize_country_text,
    parse_cart_count_value,
)


class BrowserClientConfigTests(unittest.TestCase):
    def test_failure_and_country_fallback_defaults_are_off(self):
        client = BrowserOrderClient()

        self.assertFalse(client.keep_open_on_failure)
        self.assertFalse(client.allow_detected_country_on_mismatch)

    def test_country_text_normalization(self):
        self.assertEqual(normalize_country_text("  United   Kingdom "), "united kingdom")
        self.assertEqual(normalize_country_text("HONG KONG"), "hong kong")

    def test_country_name_matches_woocommerce_labels(self):
        self.assertTrue(country_name_matches("United Kingdom (UK)", "United Kingdom"))
        self.assertTrue(country_name_matches("GB", "United Kingdom"))
        self.assertFalse(country_name_matches("United States (US) Minor Outlying Islands", "United States"))

    def test_target_closed_error_detection(self):
        client = BrowserOrderClient()

        self.assertTrue(
            client._is_target_closed_error(
                RuntimeError("Mouse.wheel: Target page, context or browser has been closed")
            )
        )
        self.assertFalse(client._is_target_closed_error(RuntimeError("some other playwright error")))

    def test_packaged_app_uses_user_browser_cache(self):
        client = BrowserOrderClient()

        with tempfile.TemporaryDirectory() as temp_dir:
            user_data_dir = Path(temp_dir) / "AutoOrderBot"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"AUTO_ORDER_BOT_USER_DATA_DIR": str(user_data_dir)}, clear=False),
            ):
                os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

                client._configure_packaged_playwright()

                browser_path = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
                self.assertEqual(browser_path, user_data_dir / "playwright-browsers")

    def test_browser_log_callback_is_optional_and_safe(self):
        messages: list[str] = []
        client = BrowserOrderClient(log_callback=messages.append)

        client._log("downloading")

        self.assertEqual(messages, ["downloading"])

    def test_existing_browser_cache_does_not_write_log(self):
        messages: list[str] = []
        client = BrowserOrderClient(log_callback=messages.append)

        with tempfile.TemporaryDirectory() as temp_dir:
            executable_path = Path(temp_dir) / "chrome.exe"
            executable_path.write_text("", encoding="utf-8")
            fake_playwright = type(
                "FakePlaywright",
                (),
                {"chromium": type("FakeChromium", (), {"executable_path": str(executable_path)})()},
            )()

            client._ensure_playwright_browser(fake_playwright)

        self.assertEqual(messages, [])

    def test_add_to_bag_selectors_cover_foenix_product_button(self):
        self.assertIn("button[data-qa='productsection-btn-addtobag']", ADD_TO_BAG_SELECTORS)
        self.assertIn(
            ".block-product__button-wrapper button.block-product__button--primary[data-qa='productsection-btn-addtobag']",
            ADD_TO_BAG_SELECTORS,
        )

    def test_place_order_selectors_cover_foenix_checkout_button(self):
        self.assertIn("button[type='submit']:has-text('Place an order')", PLACE_ORDER_SELECTORS)
        self.assertIn("button:has-text('Place an order')", PLACE_ORDER_SELECTORS)
        self.assertIn("#place_order", PLACE_ORDER_SELECTORS)
        self.assertIn("button[name='woocommerce_checkout_place_order']", PLACE_ORDER_SELECTORS)

    def test_woocommerce_product_and_cart_selectors_are_supported(self):
        self.assertIn("button.single_add_to_cart_button[name='add-to-cart']", ADD_TO_BAG_SELECTORS)
        self.assertIn(".woocommerce-message a.wc-forward:has-text('View cart')", VIEW_CART_SELECTORS)
        self.assertIn(".wc-proceed-to-checkout a.checkout-button", CART_CHECKOUT_SELECTORS)
        self.assertIn(".astra-icon.ast-icon-shopping-basket", CART_TOGGLE_SELECTORS)

    def test_cart_count_parser_supports_astra_data_attribute(self):
        self.assertEqual(parse_cart_count_value("2"), 2)
        self.assertEqual(parse_cart_count_value("Cart 12 items"), 12)
        self.assertIsNone(parse_cart_count_value(""))


if __name__ == "__main__":
    unittest.main()
