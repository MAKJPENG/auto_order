from __future__ import annotations

import unittest
from pathlib import Path

from tools import build_release


class BuildReleaseTests(unittest.TestCase):
    def test_inno_app_id_escapes_guid_opening_brace(self):
        script = build_release.make_inno_script(
            Path(r"C:\AutoOrderBot"),
            Path(r"C:\installer"),
            "0.1.0",
            "20260705-090452",
        )

        self.assertIn(f"AppId={{{{{build_release.WINDOWS_APP_ID}}}", script)
        self.assertNotIn(f"AppId={{{build_release.WINDOWS_APP_ID}}}", script)


if __name__ == "__main__":
    unittest.main()
