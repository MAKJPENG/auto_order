from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from order_bot.paths import app_data_dir, browser_cache_dir


class PathsTests(unittest.TestCase):
    def test_app_data_dir_defaults_to_current_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=False), patch("pathlib.Path.cwd", return_value=Path(temp_dir)):
                os.environ.pop("AUTO_ORDER_BOT_DATA_DIR", None)

                self.assertEqual(app_data_dir(), Path(temp_dir))

    def test_app_data_dir_can_be_overridden(self):
        with patch.dict(os.environ, {"AUTO_ORDER_BOT_DATA_DIR": r"C:\AutoOrderData"}):
            self.assertEqual(app_data_dir(), Path(r"C:\AutoOrderData"))

    def test_packaged_macos_app_uses_user_data_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            user_data = Path(temp_dir) / "AutoOrderBot"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "darwin"),
                patch.dict(os.environ, {"AUTO_ORDER_BOT_USER_DATA_DIR": str(user_data)}, clear=False),
            ):
                os.environ.pop("AUTO_ORDER_BOT_DATA_DIR", None)

                self.assertEqual(app_data_dir(), user_data)

    def test_browser_cache_dir_uses_user_data_dir(self):
        with patch.dict(os.environ, {"AUTO_ORDER_BOT_USER_DATA_DIR": r"C:\AutoOrderUserData"}, clear=False):
            os.environ.pop("AUTO_ORDER_BOT_BROWSER_CACHE_DIR", None)

            self.assertEqual(browser_cache_dir(), Path(r"C:\AutoOrderUserData") / "playwright-browsers")

    def test_browser_cache_dir_can_be_overridden(self):
        with patch.dict(os.environ, {"AUTO_ORDER_BOT_BROWSER_CACHE_DIR": r"D:\BrowserCache"}):
            self.assertEqual(browser_cache_dir(), Path(r"D:\BrowserCache"))


if __name__ == "__main__":
    unittest.main()
