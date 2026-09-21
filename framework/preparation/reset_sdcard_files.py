"""清理用户使用设备过程中产生的文件，并预置 skill 内的测试图片和视频。

清理对象包括图片、视频、下载文件及测试残留；保留 /sdcard/Android。
不使用 ZIP、不清 App 数据、不调用 UI/视觉模型；支持 Android 11+ 主共享存储。
"""
from pathlib import Path, PurePosixPath
import os
import re
import shlex
import stat
import subprocess


MEDIA_DIR = Path(__file__).resolve().parents[2] / "media-resources"
EMPTY_FOLDERS = (
    "Alarms", "Audiobooks", "Download", "DCIM", "Documents", "Music",
    "Movies", "Notifications", "Podcasts", "Pictures", "Ringtones", "Recordings",
)
MEDIA_SUFFIXES = {
    "images": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"},
    "videos": {".mp4", ".mkv", ".webm", ".mov", ".3gp"},
}


class ResetSDCardFilesError(RuntimeError):
    """文件准备未完成，调用方应中止依赖它的用例。"""


class ResetSDCardFiles:
    def __init__(self, adb_run, media_dir=None):
        # 复用 TestCase._adb_run，所有命令（包括 push）锁定同一 serial。
        self._adb_run = adb_run
        self.media_dir = Path(media_dir) if media_dir is not None else MEDIA_DIR
        self._root = None
        self._user = None

    def _run(self, *args, timeout=30):
        try:
            result = self._adb_run(*args, timeout=timeout, text=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ResetSDCardFilesError(f"ADB 执行失败: {exc}") from exc
        if result.returncode:
            message = (result.stderr or result.stdout or b"").decode("utf-8", "replace")
            raise ResetSDCardFilesError(
                f"ADB 退出码 {result.returncode}: {message.strip()[:1500]}")
        return result.stdout or b""

    def _shell(self, *args, timeout=30):
        # adb 会将 shell 后的参数再次拼接；将参数引用成一个远端命令，
        # 防止文件名中的空格、引号、$()、分号在设备 shell 上被解释。
        return self._run("shell", shlex.join(args), timeout=timeout)

    def _text(self, *args, timeout=30):
        try:
            return self._shell(*args, timeout=timeout).decode("utf-8").strip()
        except UnicodeError as exc:
            raise ResetSDCardFilesError("ADB 返回了无法解析的 UTF-8 输出") from exc

    def _prepare_device(self):
        root = self._text("readlink", "-f", "/sdcard")
        match = re.fullmatch(r"/storage/emulated/([0-9]+)", root)
        if not match:
            raise ResetSDCardFilesError(f"拒绝清理未知的 /sdcard 实际路径: {root!r}")
        user = match.group(1)
        if self._text("am", "get-current-user") != user:
            raise ResetSDCardFilesError("/sdcard 与当前 Android 用户不一致，未执行清理")
        sdk = self._text("getprop", "ro.build.version.sdk")
        if not sdk.isascii() or not sdk.isdigit() or int(sdk) < 30:
            raise ResetSDCardFilesError("文件重置的媒体扫描需要 Android 11 或以上版本")
        self._shell("test", "-d", root)
        self._root, self._user = root, user

    def _entries(self, directory):
        raw = self._shell("find", directory, "-mindepth", "1", "-maxdepth", "1", "-print0")
        if raw and not raw.endswith(b"\0"):
            raise ResetSDCardFilesError(f"目录清单不完整: {directory}")
        try:
            entries = [p.decode("utf-8") for p in raw.split(b"\0") if p]
        except UnicodeError as exc:
            raise ResetSDCardFilesError(f"目录包含无法解析的文件名: {directory}") from exc
        for entry in entries:
            # 不接受 find 输出中的根目录、..、越界条目或不规范路径。
            path = PurePosixPath(entry)
            if (str(path.parent) != directory or str(path) != entry
                    or path.name in (".", "..") or ".." in path.parts):
                raise ResetSDCardFilesError(f"拒绝清理越界目录条目: {entry!r}")
        return entries

    def _real_directory(self, path):
        self._shell("test", "!", "-L", path)
        self._shell("test", "-d", path)
        if self._text("readlink", "-f", path) != path:
            raise ResetSDCardFilesError(f"拒绝进入重定向目录: {path}")

    def _clear_files(self):
        # 删除前先完成目录检查及计划；不通过 glob 清理，包含隐藏文件。
        targets = []
        for path in self._entries(self._root):
            name = PurePosixPath(path).name
            if name == "Android":
                continue
            if name in EMPTY_FOLDERS:
                self._real_directory(path)
                targets.extend(self._entries(path))
            else:
                targets.append(path)
        for path in targets:
            # rm 删除链接本身，不跟随它；受保护目录从不作为删除目标。
            self._shell("rm", "-rf", "--", path, timeout=120)
        # 系统可能立即重建缓存；清理后不要求目录持续为空。
        for name in EMPTY_FOLDERS:
            self._shell("mkdir", "-p", f"{self._root}/{name}")

    def _scan(self):
        # Android 11+ MediaStore.scanVolume 的 ADB 入口，为阻塞扫描；
        # 同时处理新增文件和已删除文件的索引，不依赖旧的单文件广播扫目录。
        output = self._text(
            "content", "call", "--user", self._user, "--uri", "content://media",
            "--method", "scan_volume", "--arg", "external_primary", timeout=120)
        # content 命令可能以退出码 0 打印 Java 异常，不能仅看 returncode。
        if not re.fullmatch(r"Result: Bundle\[.*\]", output, re.DOTALL):
            raise ResetSDCardFilesError(f"媒体扫描未返回成功结果: {output[:1500]}")

    def _local_files(self):
        files = {}
        try:
            if self.media_dir.is_symlink() or not self.media_dir.is_dir():
                raise ResetSDCardFilesError(f"素材目录不存在或为链接: {self.media_dir}")
            root = self.media_dir.resolve()
            for folder, suffixes in MEDIA_SUFFIXES.items():
                base = root / folder
                if base.is_symlink() or base.resolve() != base or not base.is_dir():
                    raise ResetSDCardFilesError(f"缺少素材目录或目录为链接: {base}")
                count = 0
                for current, dirs, names in os.walk(
                        base, followlinks=False, onerror=self._walk_error):
                    for name in dirs + names:
                        path = Path(current) / name
                        if path.is_symlink() or path.resolve() != path:
                            raise ResetSDCardFilesError(f"素材路径越界或为链接: {path}")
                    for name in names:
                        path = Path(current) / name
                        if path.suffix.lower() not in suffixes or not stat.S_ISREG(path.stat().st_mode):
                            raise ResetSDCardFilesError(f"不支持的素材文件: {path}")
                        # 在清理设备前完整读取，尽早发现空文件、权限和读取错误。
                        size = 0
                        with path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                size += len(chunk)
                        if not size:
                            raise ResetSDCardFilesError(f"素材文件为空: {path}")
                        files[path.relative_to(root).as_posix()] = (path, size)
                        count += 1
                if not count:
                    raise ResetSDCardFilesError(f"素材目录为空: {base}")
        except OSError as exc:
            raise ResetSDCardFilesError(f"读取本地素材失败: {exc}") from exc
        return dict(sorted(files.items()))

    @staticmethod
    def _walk_error(error):
        raise error

    def _push_media(self, files):
        base = f"{self._root}/media-resources"
        # 同目录下的多个文件只创建一次父目录。
        parents = {str(PurePosixPath(base, relative).parent) for relative in files}
        for parent in sorted(parents):
            self._shell("mkdir", "-p", parent)
        for relative, (source, _) in files.items():
            self._run("push", str(source), f"{base}/{relative}", timeout=120)

    def _verify_media(self, expected):
        base = f"{self._root}/media-resources"
        actual = {}

        pending = [base]
        while pending:
            directory = pending.pop()
            self._real_directory(directory)
            for path in self._entries(directory):
                # 一次 stat 读取类型与大小；不跟随符号链接。
                kind, _, size = self._text("stat", "-c", "%F:%s", path).partition(":")
                if kind == "directory":
                    pending.append(path)
                elif kind == "regular file":
                    if not size.isascii() or not size.isdigit():
                        raise ResetSDCardFilesError(f"无法核对文件大小: {path}")
                    actual[path[len(base) + 1:]] = int(size)
                else:
                    raise ResetSDCardFilesError(f"设备素材不是普通文件/目录: {path}")

        wanted = {name: size for name, (_, size) in expected.items()}
        if actual != wanted:
            missing = sorted(wanted.keys() - actual.keys())
            extra = sorted(actual.keys() - wanted.keys())
            changed = sorted(k for k in wanted.keys() & actual.keys() if wanted[k] != actual[k])
            raise ResetSDCardFilesError(
                f"素材核对失败: 缺失={missing}, 多余={extra}, 大小不符={changed}")

    def clear(self):
        """清理设备使用过程中产生的用户文件并刷新媒体索引，不依赖本地素材。"""
        self._prepare_device()
        self._clear_files()
        self._scan()
        return {"files": 0, "bytes": 0}

    def reset(self):
        """清理后推送 images/videos；先检查本机素材，再触碰设备。"""
        files = self._local_files()
        self._prepare_device()
        self._clear_files()
        self._push_media(files)
        self._scan()
        self._verify_media(files)
        return {"files": len(files), "bytes": sum(size for _, size in files.values())}
