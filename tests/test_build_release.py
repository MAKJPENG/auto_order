from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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

    def test_installers_do_not_bundle_playwright_browser(self):
        self.assertFalse(build_release.should_bundle_playwright_browser("windows"))
        self.assertFalse(build_release.should_bundle_playwright_browser("mac"))

    def test_mac_app_executable_uses_internal_binary_name(self):
        app_path = Path("/tmp/自动下单机器人.app")

        self.assertEqual(
            build_release.mac_app_executable(app_path),
            app_path / "Contents" / "MacOS" / build_release.APP_INTERNAL_NAME,
        )

    def test_mac_delivery_readme_defaults_to_pkg_only(self):
        with TemporaryDirectory() as temp_dir:
            build_release.write_mac_delivery_readme(Path(temp_dir), "0.1.0", "20260705-120000", include_dmg=False)

            message = (Path(temp_dir) / "发给别人看这里.txt").read_text(encoding="utf-8")

        self.assertIn("本次未选择生成 DMG", message)
        self.assertIn(".pkg", message)

    def test_mac_delivery_readme_mentions_dmg_when_requested(self):
        with TemporaryDirectory() as temp_dir:
            build_release.write_mac_delivery_readme(Path(temp_dir), "0.1.0", "20260705-120000", include_dmg=True)

            message = (Path(temp_dir) / "发给别人看这里.txt").read_text(encoding="utf-8")

        self.assertIn("本次已选择生成 DMG", message)
        self.assertIn(".dmg", message)


if __name__ == "__main__":
    unittest.main()
