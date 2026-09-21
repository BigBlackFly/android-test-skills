"""目标 App 的运行时权限与 AppOps 批量授权。"""
from dataclasses import dataclass, field
import re
import shlex
import subprocess

SPECIAL_PERMISSION_OPS = {
    "android.permission.MANAGE_EXTERNAL_STORAGE": "MANAGE_EXTERNAL_STORAGE",
    "android.permission.MANAGE_MEDIA": "MANAGE_MEDIA",
    "android.permission.MANAGE_IPSEC_TUNNELS": "MANAGE_IPSEC_TUNNELS",
    "android.permission.TURN_SCREEN_ON": "TURN_SCREEN_ON",
    "android.permission.USE_ICC_AUTH_WITH_DEVICE_IDENTIFIER": "USE_ICC_AUTH_WITH_DEVICE_IDENTIFIER",
    "android.permission.MANAGE_ONGOING_CALLS": "MANAGE_ONGOING_CALLS",
    "android.permission.INTERACT_ACROSS_PROFILES": "INTERACT_ACROSS_PROFILES",
    "android.permission.SYSTEM_ALERT_WINDOW": "SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_SETTINGS": "WRITE_SETTINGS",
    "android.permission.SCHEDULE_EXACT_ALARM": "SCHEDULE_EXACT_ALARM",
    "android.permission.REQUEST_INSTALL_PACKAGES": "REQUEST_INSTALL_PACKAGES",
    "android.permission.MEDIA_ROUTING_CONTROL": "MEDIA_ROUTING_CONTROL",
    "android.permission.LOADER_USAGE_STATS": "LOADER_USAGE_STATS",
    "android.permission.ACCESS_NOTIFICATIONS": "ACCESS_NOTIFICATIONS",
    "android.permission.FOREGROUND_SERVICE_SPECIAL_USE": "FOREGROUND_SERVICE_SPECIAL_USE",
    "android.permission.SMS_FINANCIAL_TRANSACTIONS": "SMS_FINANCIAL_TRANSACTIONS",
    "android.permission.USE_FULL_SCREEN_INTENT": "USE_FULL_SCREEN_INTENT",
    "android.permission.RECEIVE_SANDBOX_TRIGGER_AUDIO": "RECEIVE_SANDBOX_TRIGGER_AUDIO",
    "android.permission.CAPTURE_CONSENTLESS_BUGREPORT_ON_USERDEBUG_BUILD": "CAPTURE_CONSENTLESS_BUGREPORT_ON_USERDEBUG_BUILD",
    "android.permission.PACKAGE_USAGE_STATS": "GET_USAGE_STATS",
    "android.permission.INSTANT_APP_FOREGROUND_SERVICE": "INSTANT_APP_START_FOREGROUND",
}
_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
_APP_OP = re.compile(r"(?:Uid mode:\s*)?([A-Z][A-Z0-9_]*):\s*"
                     r"(allow|ignore|deny|default|foreground|errored)\b")
_COMMAND_ERROR = re.compile(
    r"^[ \t]*(?:Error:|Exception|Failure\b|Unknown operation|Unknown package|"
    r"java\.[\w.]+Exception)[^\r\n]*", re.MULTILINE | re.IGNORECASE)


class AppPermissionError(RuntimeError):
    """授权执行或状态检查异常。"""


@dataclass
class PackagePermissions:
    requested: set = field(default_factory=set)
    install: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)

    @property
    def not_granted(self):
        return {name for name in self.requested
                if not self.install.get(name, False) and not self.runtime.get(name, False)}


def parse_package_permissions(output, package, user):
    """只读取目标包、目标用户；通过缩进界定段落，排除其他包和用户。"""
    state = PackagePermissions()
    package_indent = None
    section, section_indent, current_user = None, -1, None
    installed = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        if package_indent is None:
            if re.fullmatch(r"Package \[" + re.escape(package) + r"\] \([^)]*\):", line):
                package_indent = indent
            continue
        if indent <= package_indent:
            break
        match = re.match(r"User (\d+):\s*(.*)", line)
        if match:
            current_user = match.group(1)
            section = None
            if current_user == str(user):
                installed = bool(re.search(r"\binstalled=true\b", match.group(2)))
            continue
        if section and indent <= section_indent:
            section = None
        if line in ("requested permissions:", "install permissions:", "runtime permissions:"):
            section = line.split()[0]
            section_indent = indent
            continue
        if section == "requested":
            if not re.fullmatch(_NAME, line):
                raise AppPermissionError(f"无法解析请求权限: {line}")
            state.requested.add(line)
        elif section in ("install", "runtime"):
            if section == "runtime" and current_user != str(user):
                continue
            match = re.match(r"(" + _NAME + r"): granted=(true|false)\b", line)
            if not match:
                raise AppPermissionError(f"无法解析权限状态: {line}")
            getattr(state, section)[match.group(1)] = match.group(2) == "true"
    if package_indent is None or not installed:
        raise AppPermissionError(f"未找到当前用户 {user} 已安装的 App: {package}")
    return state


def parse_appops(output):
    """保留重复操作的状态（UID 与包级可能不同）；跳过时间明细和无操作提示。"""
    result = []
    for raw in output.splitlines():
        line = raw.strip()
        match = _APP_OP.match(line)
        if match:
            result.append(match.groups())
        elif re.match(r"(?:Uid mode:\s*)?[A-Z][A-Z0-9_]*:", line):
            raise AppPermissionError(f"无法解析 AppOps 状态: {line}")
    return result


class AppPermissionGranter:
    def __init__(self, adb_run):
        # 由调用方注入绑定 serial 的执行接口。
        self._adb_run = adb_run

    def _execute(self, command, timeout=30):
        try:
            result = self._adb_run("shell", command, timeout=timeout, text=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AppPermissionError(f"权限准备命令执行失败: {exc}") from exc
        output = (result.stdout or b"").decode("utf-8", "replace").strip()
        error = (result.stderr or b"").decode("utf-8", "replace").strip()
        # 部分 Android shell 工具以退出码 0 返回错误文本。
        bad_output = _COMMAND_ERROR.search(output + "\n" + error)
        if result.returncode or bad_output:
            detail = bad_output.group().strip() if bad_output else (error or output)
            raise AppPermissionError(
                f"权限准备命令失败（退出码 {result.returncode}，{command}）: {detail[:1500]}")
        return output

    def _shell(self, *args, timeout=30):
        return self._execute(shlex.join(args), timeout=timeout)

    def _verify_appop(self, package, user, op):
        output = self._shell("appops", "get", "--user", user, package, op)
        modes = [mode for name, mode in parse_appops(output) if name == op]
        # 无显式记录时，使用查询返回的默认模式。
        if not modes and re.search(r"(?m)^[ \t]*Default mode:[ \t]*allow[ \t]*$", output):
            return
        if not modes or any(mode != "allow" for mode in modes):
            raise AppPermissionError(f"AppOps {op} 未生效: {output[:1000]}")

    def _permissions(self, package, user):
        return parse_package_permissions(
            self._shell("dumpsys", "package", package), package, user)

    def _grant_runtime_permissions(self, package, user, before):
        self._shell("pm", "grant", "--user", user, "--all-permissions", package, timeout=120)
        after = self._permissions(package, user)
        # 批量授权可能不返回单项错误，需核对权限状态。
        runtime = before.runtime.keys() | after.runtime.keys()
        remaining = sorted(runtime & after.not_granted)
        if remaining:
            raise AppPermissionError(f"运行时权限仍未授予: {', '.join(remaining)}")
        return after

    def _verify_appops(self, package, user, ops):
        # 统一读取显式状态；缺少记录时单独查询该项的默认模式。
        output = self._shell("appops", "get", "--user", user, package)
        modes = {}
        for op, mode in parse_appops(output):
            modes.setdefault(op, set()).add(mode)
        for op in ops:
            if op not in modes:
                self._verify_appop(package, user, op)
            elif modes[op] != {"allow"}:
                raise AppPermissionError(f"AppOps {op} 未生效: {output[:1000]}")

    def _grant_appops(self, package, user, permissions):
        special = {SPECIAL_PERMISSION_OPS[name] for name in permissions.not_granted
                   if name in SPECIAL_PERMISSION_OPS}
        appops = parse_appops(self._shell("appops", "get", "--user", user, package))
        # 查询中的 allow/ignore 不参与补全；特殊权限映射独立加入授权集合。
        ops = sorted(special | {op for op, mode in appops if mode not in ("allow", "ignore")})
        if ops:
            # 单次会话批处理；任意一项失败即停止，保留失败命令及退出码。
            commands = [shlex.join(("appops", "set", "--user", user, package, op, "allow"))
                        + " || exit $?" for op in ops]
            self._execute("\n".join(commands), timeout=120)
            self._verify_appops(package, user, ops)
        ignored = sorted({op for op, mode in appops if mode == "ignore"} - set(ops))
        return ops, ignored

    def grant(self, package):
        """批量授予运行时权限并补全特殊 AppOps，返回处理及未覆盖项目的清单。"""
        if not isinstance(package, str) or not re.fullmatch(_NAME, package):
            raise AppPermissionError(f"无效的目标包名: {package!r}")
        user = self._shell("am", "get-current-user")
        if not user.isascii() or not user.isdigit():
            raise AppPermissionError(f"无法确定当前 Android 用户: {user!r}")
        before = self._permissions(package, user)
        if "--all-permissions" not in self._shell("pm", "help"):
            raise AppPermissionError("此设备不支持 pm grant --all-permissions，未执行授权")

        after = self._grant_runtime_permissions(package, user, before)
        ops, ignored = self._grant_appops(package, user, after)
        return {
            "package": package,
            "user": user,
            "runtime_permissions": sorted(name for name, granted in after.runtime.items() if granted),
            "appops": ops,
            "unhandled_permissions": sorted(after.not_granted - SPECIAL_PERMISSION_OPS.keys()),
            "ignored_appops": ignored,
        }
