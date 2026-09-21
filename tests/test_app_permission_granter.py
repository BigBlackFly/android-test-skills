"""权限输出的纯文本解析测试；真实授权见 check_preparation_device.py。"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "framework"))
from preparation.grant_app_permissions import (
    AppPermissionError, parse_appops, parse_package_permissions,
)

PKG = "com.example.gallery"
CAMERA = "android.permission.CAMERA"
SPECIAL = "android.permission.MANAGE_EXTERNAL_STORAGE"


def package_dump(requested=(), runtime=None, install=None, user="10", installed=True):
    runtime, install = runtime or {}, install or {}
    lines = ["Packages:", f"  Package [{PKG}] (ab12):", "    requested permissions:"]
    lines += ["      " + name for name in requested]
    lines += ["    install permissions:"]
    lines += [f"      {name}: granted={str(granted).lower()}" for name, granted in install.items()]
    lines += [f"    User {user}: installed={str(installed).lower()} hidden=false",
              "      runtime permissions:"]
    lines += [f"        {name}: granted={str(granted).lower()}, flags=[ USER_SET]"
              for name, granted in runtime.items()]
    return "\n".join(lines) + "\n"


class TestPermissionParsing(unittest.TestCase):
    def test_package_and_current_user_are_isolated(self):
        dump = package_dump([CAMERA, SPECIAL], {CAMERA: False}, user="10")
        dump += ("    User 0: installed=true\n      runtime permissions:\n"
                 f"        {CAMERA}: granted=true, flags=[]\n"
                 "  Package [com.other.app] (ac):\n    requested permissions:\n"
                 "      android.permission.RECORD_AUDIO\n")
        result = parse_package_permissions(dump, PKG, "10")
        self.assertEqual(result.requested, {CAMERA, SPECIAL})
        self.assertEqual(result.runtime, {CAMERA: False})
        self.assertEqual(result.not_granted, {CAMERA, SPECIAL})

    def test_install_grants_section_boundaries_and_crlf(self):
        dump = package_dump([CAMERA, SPECIAL], {CAMERA: False}, {SPECIAL: True})
        dump += "      enabledComponents:\n        com.example.Component\nQueries:\n  User 10:\n"
        result = parse_package_permissions(dump.replace("\n", "\r\r\n"), PKG, "10")
        self.assertEqual(result.not_granted, {CAMERA})

    def test_no_permissions_is_valid_but_missing_user_or_package_is_not(self):
        self.assertEqual(parse_package_permissions(package_dump(), PKG, "10").requested, set())
        for output in ("Unable to find package", package_dump(user="0"),
                       package_dump(installed=False)):
            with self.subTest(output=output), self.assertRaises(AppPermissionError):
                parse_package_permissions(output, PKG, "10")

    def test_malformed_permission_state_is_not_silently_accepted(self):
        dump = package_dump([CAMERA], {CAMERA: False}).replace("granted=false", "granted=unknown")
        with self.assertRaises(AppPermissionError):
            parse_package_permissions(dump, PKG, "10")

    def test_appops_uid_prefix_modes_details_and_empty_output(self):
        output = ("Uid mode: CAMERA: foreground\r\nREAD_CONTACTS: ignore\n\n"
                  "CAMERA: allow; time=+1s ago\nSYSTEM_ALERT_WINDOW: default\n"
                  "  Attribution: tag\n    time=+2s ago\nGET_USAGE_STATS: deny\n")
        self.assertEqual(parse_appops(output), [("CAMERA", "foreground"),
                         ("READ_CONTACTS", "ignore"), ("CAMERA", "allow"),
                         ("SYSTEM_ALERT_WINDOW", "default"), ("GET_USAGE_STATS", "deny")])
        for empty in ("", "\n", "No operations.\n", "Uid mode:\nNo operations."):
            self.assertEqual(parse_appops(empty), [])
        with self.assertRaises(AppPermissionError):
            parse_appops("CAMERA: unexpected")


if __name__ == "__main__":
    unittest.main()
