"""本地媒体资源校验；不执行或模拟设备命令。"""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "framework"))
from preparation import reset_sdcard_files as files


class TestLocalMediaResources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / "media-resources"
        (self.media / "images").mkdir(parents=True)
        (self.media / "videos").mkdir()
        self.photo = self.media / "images/photo.jpg"
        self.photo.write_bytes(b"image fixture")
        (self.media / "videos/clip.mp4").write_bytes(b"video fixture")
        (self.media / "README.md").write_text("not a device resource", encoding="utf-8")
        # 仅测试本地素材读取，不提供设备执行接口。
        self.worker = files.ResetSDCardFiles(adb_run=None, media_dir=self.media)

    def test_manifest_contains_media_with_correct_sizes(self):
        manifest = self.worker._local_files()
        self.assertEqual(set(manifest), {"images/photo.jpg", "videos/clip.mp4"})
        for source, size in manifest.values():
            self.assertEqual(size, len(source.read_bytes()))

    def test_nested_and_unicode_names_preserve_relative_paths(self):
        directory = self.media / "images/nested folder"
        directory.mkdir()
        (directory / "用户的照片's.png").write_bytes(b"nested image")
        self.assertIn("images/nested folder/用户的照片's.png", self.worker._local_files())

    def test_missing_and_empty_directories_are_rejected(self):
        missing = files.ResetSDCardFiles(adb_run=None, media_dir=self.media / "missing")
        with self.assertRaises(files.ResetSDCardFilesError):
            missing._local_files()
        self.photo.unlink()
        with self.assertRaisesRegex(files.ResetSDCardFilesError, "素材目录为空"):
            self.worker._local_files()

    def test_empty_and_unsupported_files_are_rejected(self):
        self.photo.write_bytes(b"")
        with self.assertRaisesRegex(files.ResetSDCardFilesError, "素材文件为空"):
            self.worker._local_files()
        self.photo.write_bytes(b"image")
        (self.media / "images/.nomedia").write_bytes(b"hidden")
        with self.assertRaisesRegex(files.ResetSDCardFilesError, "不支持的素材文件"):
            self.worker._local_files()

    def test_linked_resource_is_rejected(self):
        link = self.media / "images/link.jpg"
        try:
            link.symlink_to(self.photo)
        except OSError as exc:
            self.skipTest(f"当前系统不允许创建测试符号链接: {exc}")
        with self.assertRaisesRegex(files.ResetSDCardFilesError, "路径越界或为链接"):
            self.worker._local_files()

    def test_bundled_resources_resolve_from_skill(self):
        self.assertEqual(files.MEDIA_DIR, ROOT / "media-resources")
        manifest = files.ResetSDCardFiles(adb_run=None)._local_files()
        self.assertEqual(sum(k.startswith("images/") for k in manifest), 4)
        self.assertEqual(sum(k.startswith("videos/") for k in manifest), 2)
        self.assertLess(sum(size for _, size in manifest.values()), 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
