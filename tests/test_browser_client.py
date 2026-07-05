from __future__ import annotations

import unittest

from order_bot.browser_client import BrowserOrderClient, normalize_country_text


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


if __name__ == "__main__":
    unittest.main()
