#!/usr/bin/env python3
"""真机验收：清理设备用户文件、预置媒体、清理目标 App 数据并授权。

独立入口，不参与 unittest discovery；必须指定设备和目标包名。
结果写入独立工作区，结束时设备保留预置的测试图片和视频。
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="测试平板序列号")
    parser.add_argument("--package", required=True, help="清理数据并授权的目标 App 包名")
    parser.add_argument("--output", type=Path, help="验收产物目录，默认 storage/preparation-device/<时间>")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", args.package):
        parser.error("目标包名格式无效")
    sys.path.insert(0, str(ROOT / "framework"))
    token = uuid.uuid4().hex[:12]
    output = (args.output or ROOT / "storage/preparation-device" /
              (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + token)).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["DSH_WORKSPACE_DIR"] = str(output)
    os.environ["DSH_TRACE"] = "0"
    for key in ("DSH_CASE_USER_INPUT", "DSH_CASE_SCRIPT_PATH", "DSH_STOP_AFTER", "DSH_SUITE_ID"):
        os.environ.pop(key, None)
    import test_framework as framework
    from preparation.reset_sdcard_files import MEDIA_DIR, MEDIA_SUFFIXES
    from preparation.grant_app_permissions import parse_package_permissions

    report = {"device": args.device, "package": args.package, "checks": [], "status": "RUNNING"}
    case = None
    protected = None
    must_restore = False
    app_marker = None

    def adb(*parts, timeout=120):
        result = subprocess.run(["adb", "-s", args.device, *parts], capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode("utf-8", "replace"))
        return result.stdout

    def shell(*parts):
        return adb("shell", shlex.join(parts))

    def text(*parts):
        return shell(*parts).decode("utf-8", "replace").strip()

    def check(name, condition, **details):
        status = "PASS" if condition else "FAIL"
        report["checks"].append({"name": name, "status": status, **details})
        print(f"[{status}] {name}", flush=True)
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def exists(path):
        return text("sh", "-c", f"if test -e {shlex.quote(path)}; then echo yes; else echo no; fi") == "yes"

    def permission_state(label):
        raw = text("dumpsys", "package", args.package)
        (output / f"permissions_{label}.txt").write_text(raw, encoding="utf-8")
        return parse_package_permissions(raw, args.package, user)

    def media_index():
        raw = text("content", "query", "--user", user, "--uri", "content://media/external_primary/file",
                   "--projection", "_data:media_type", "--where", "media_type=1 OR media_type=3")
        if raw == "No result found.":
            return set()
        paths = set()
        for line in raw.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"Row: \d+ _data=(.*), media_type=[13]", line.strip())
            if not match:
                raise RuntimeError(f"无法解析媒体索引: {line}")
            path = match.group(1)
            if path.startswith(root + "/") and not path.startswith(root + "/Android/"):
                paths.add(path)
        return paths

    def verify_presets(label):
        base = root + "/media-resources"
        actual = {p.decode("utf-8") for p in shell("find", base, "-type", "f", "-print0").split(b"\0") if p}
        expected = {base + "/" + name for name in resources}
        check(label + "：文件清单", actual == expected, files=len(actual))
        mismatched = []
        for name, digest in resources.items():
            actual_hash = text("sha256sum", base + "/" + name).split()[0]
            if actual_hash != digest:
                mismatched.append(name)
        check(label + "：文件内容", not mismatched, mismatched=mismatched)
        indexed = media_index()
        check(label + "：媒体索引", indexed == expected,
              indexed_files=len(indexed), missing=sorted(expected - indexed), extra=sorted(indexed - expected))
        check(label + "：Android 目录保留", text("cat", protected) == token)

    try:
        check("设备在线", adb("get-state").strip() == b"device")
        user = text("am", "get-current-user")
        root = text("readlink", "-f", "/sdcard")
        check("设备存储与当前用户一致", user.isascii() and user.isdigit() and root == f"/storage/emulated/{user}")
        report["model"] = text("getprop", "ro.product.model")
        report["sdk"] = text("getprop", "ro.build.version.sdk")
        initial = permission_state("initial")
        resources = {}
        for folder, suffixes in MEDIA_SUFFIXES.items():
            candidates = [p for p in (MEDIA_DIR / folder).rglob("*") if p.is_file() and p.suffix.lower() in suffixes]
            check(f"本地测试资源：{folder}", bool(candidates))
            for path in candidates:
                resources[path.relative_to(MEDIA_DIR).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        report["resources"] = resources
        print(f"目标：{args.device} / {args.package}；开始真实文件清理和 App 数据准备。", flush=True)

        marker = output / "marker.txt"
        marker.write_text(token, encoding="utf-8")
        protected = f"{root}/Android/media/dsh.preparation.check/{token}/keep.txt"
        residue = [f"{root}/Download/.dsh-preparation-{token}.txt",
                   f"{root}/dsh-preparation-{token}/residue.txt"]
        for path in [protected, *residue]:
            shell("mkdir", "-p", path.rsplit("/", 1)[0])
            adb("push", str(marker), path)

        app_marker = f"{root}/Android/data/{args.package}/files/.dsh-preparation-{token}.txt"
        shell("mkdir", "-p", app_marker.rsplit("/", 1)[0])
        adb("push", str(marker), app_marker)
        check("已创建目标 App 的历史数据", exists(app_marker))

        case = framework.TestCase("测试环境准备真机验收", device_id=args.device, user_input="",
                                  target_package=args.package)
        expected = ["reset_sdcard_files", "set_target_package", "pm_clear", "grant_permissions"]
        actions = [a["action"] for step in case.steps for a in step.get("actions", [])
                   if a["action"] in expected]
        check("初始化按固定顺序执行四项准备", actions == expected, actions=actions)
        check("准备步骤不占用测试步骤额度", len(case.steps) == 4 and all(s.get("preparation") for s in case.steps))
        check("框架使用显式传入的包名", case.target_package == args.package, actual=case.target_package)
        check("报告使用准备阶段确定的 App", case._case_package_from_script() == args.package)
        check("初始化清除目标 App 历史数据", not exists(app_marker))
        after = permission_state("after_initialization")
        remaining = sorted((initial.runtime.keys() | after.runtime.keys()) & after.not_granted)
        check("初始化已授予运行时权限", not remaining, remaining=remaining, after=after.runtime)
        if case.permissions_granted is False:
            warning = case.steps[-1]["results"][-1]
            check("初始化授权未完全生效时记录 WARN", warning["result"] == "WARN")
            report["permission_warning"] = warning["detail"]
        else:
            check("初始化批量授权返回成功", case.permissions_granted is True)
        check("初始化删除测试残留", all(not exists(path) for path in residue))
        verify_presets("初始化预置")

        case.step("清理设备中的图片、视频及测试残留")
        must_restore = True
        check("clear_sdcard_files 返回成功", case.clear_sdcard_files() is True)
        check("预置文件已删除", all(not exists(root + "/media-resources/" + name) for name in resources))
        remaining = media_index()
        check("清理后图片视频索引为空", not remaining, remaining=sorted(remaining))
        check("清理后 Android 目录保留", text("cat", protected) == token)

        case.step("重新预置测试图片和视频")
        check("reset_sdcard_files 返回成功", case.reset_sdcard_files() is True)
        verify_presets("再次预置")
        must_restore = False

        case.step("再次清理 App 数据后自动恢复权限")
        before = permission_state("before_second_clear")
        check("pm_clear 返回成功", case.pm_clear(args.package) is True)
        actions = [a["action"] for a in case.steps[-1].get("actions", [])]
        check("再次清数据后自动授权", actions == ["pm_clear", "grant_permissions"], actions=actions)
        after = permission_state("after_second_clear")
        remaining = sorted((before.runtime.keys() | after.runtime.keys()) & after.not_granted)
        check("再次清数据后运行时权限恢复", not remaining, remaining=remaining, after=after.runtime)
        (output / "appops_after_grant.txt").write_text(
            text("appops", "get", "--user", user, args.package), encoding="utf-8")

        case.step("授权失败不终止后续执行")
        missing_package = "com.dsh.preparation.missing_" + token
        check("不存在的 App 授权返回 False", case.grant_permissions(missing_package) is False)
        check("授权失败记录 WARN", case.steps[-1]["results"][-1]["result"] == "WARN")
        case.record("INFO", "授权失败后已继续执行", evidence=False)
        check("授权失败后继续执行", case.steps[-1]["results"][-1]["result"] == "INFO")
        # 即使名称、描述或目录包含 App 信息，框架也只接受显式传入的包名。
        shell("mkdir", "-p", app_marker.rsplit("/", 1)[0])
        adb("push", str(marker), app_marker)
        before_skip = permission_state("before_missing_package")
        context_script = output / "cases" / args.package / "_context.py"
        context_script.parent.mkdir(parents=True, exist_ok=True)
        context_script.write_text("# 仅用于验证路径不参与设备准备\n", encoding="utf-8")
        for label, context in (
            ("未传包名_图库", {"user_input": f"待测应用：图库\n包名：{args.package}"}),
            ("仅有包名目录", {"script_path": str(context_script)}),
            ("误传应用名称", {"target_package": "图库"}),
        ):
            options = {"user_input": "", **context}
            skipped = framework.TestCase(label, device_id=args.device, **options)
            try:
                actions = [a["action"] for step in skipped.steps for a in step.get("actions", [])
                           if a["action"] in expected]
                check(label + "时仅执行文件准备和包名检查",
                      actions == ["reset_sdcard_files", "set_target_package"], actions=actions)
                check(label + "时记录 INFO 提示",
                      skipped.steps[-1]["results"][-1]["result"] == "INFO")
                check(label + "时不设置目标包名和授权结果",
                      skipped.target_package is None and skipped.permissions_granted is None)
                check(label + "时保留 App 历史数据", text("cat", app_marker) == token)
                after_skip = permission_state("after_" + label)
                check(label + "时不改变运行时权限", after_skip.runtime == before_skip.runtime)
                skipped.step("验证后续测试步骤")
                skipped.record("INFO", "App 准备已跳过，测试步骤正常执行", evidence=False)
                check(label + "后可继续执行测试步骤",
                      not skipped.steps[-1].get("preparation", False) and skipped._compute_final_status() == "PASS")
            finally:
                skipped.finish()

        # 已确定包名但清数据失败仍须中止；使用未安装的包验证真实失败路径。
        try:
            framework.TestCase("目标未安装", device_id=args.device, user_input="", target_package=missing_package)
        except framework.CaseAbort:
            failed = framework.LAST_CASE
            actions = [a["action"] for step in failed.steps for a in step.get("actions", [])
                       if a["action"] in expected]
            check("目标未安装时中止后续准备与测试步骤",
                  actions == ["reset_sdcard_files", "set_target_package", "pm_clear"]
                  and all(step.get("preparation") for step in failed.steps), actions=actions)
            failed.finish()
        else:
            framework.LAST_CASE.finish()
            check("目标未安装时必须中止", False)
        report["status"] = "WARN" if report.get("permission_warning") else "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], flush=True)
        if case is None:
            case = getattr(framework, "LAST_CASE", None)
        if case is not None:
            case._fatal_error = report["error"]
    finally:
        if must_restore and case is not None:
            try:
                case.reset_sdcard_files()
                report["restored_presets"] = True
            except Exception as exc:
                report["restore_error"] = str(exc)
        if app_marker is not None:
            try:
                shell("rm", "-f", "--", app_marker)
            except Exception as exc:
                report["app_marker_cleanup_error"] = str(exc)
                report["status"] = "FAIL"
        if protected is not None:
            try:
                shell("rm", "-f", "--", protected)
                shell("rmdir", protected.rsplit("/", 1)[0])
                # 仅移除本次创建的空目录，不触碰其他 Android 文件。
                shell("rmdir", root + "/Android/media/dsh.preparation.check")
            except Exception as exc:
                report["marker_cleanup_error"] = str(exc)
                report["status"] = "FAIL"
        if case is not None:
            try:
                report["case_report"] = case.finish()
                report["case_status"] = case.final_status
            except Exception as exc:
                report["finish_error"] = str(exc)
                report["status"] = "FAIL"
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"验收结果：{report['status']}；报告：{output / 'result.json'}", flush=True)
    return 0 if report["status"] in ("PASS", "WARN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
