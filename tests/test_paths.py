from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from order_bot.paths import app_data_dir


class PathsTests(unittest.TestCase):
    def test_app_data_dir_defaults_to_current_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=False), patch("pathlib.Path.cwd", return_value=Path(temp_dir)):
                os.environ.pop("AUTO_ORDER_BOT_DATA_DIR", None)

                self.assertEqual(app_data_dir(), Path(temp_dir))

    def test_app_data_dir_can_be_overridden(self):
        with patch.dict(os.environ, {"AUTO_ORDER_BOT_DATA_DIR": r"C:\AutoOrderData"}):
            self.assertEqual(app_data_dir(), Path(r"C:\AutoOrderData"))


if __name__ == "__main__":
    unittest.main()
