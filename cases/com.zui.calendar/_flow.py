#!/usr/bin/env python3
"""com.zui.calendar 可复用流程（flows）——本 App 多个用例共享，避免各自复制。

组织约定（cases 按被测 App 包名分目录）：
- 每个 App 一个 cases/<包名>/_flow.py；目录名 = 包名，与 knowledge/<包名>.md 同键
- 只放"入口/前置链路"，断言逻辑留给各用例自己写
- 准入规则：≥2 个用例共享的流程才提取到这里，一次性链路留在用例文件里
- 每个函数返回 bool，失败时已自行 record/blocked
- 生成新用例时：先查 knowledge/<包名>.md 的「标准链路」，命中即在这里找现成函数

已有流程：
  goto_课程表空状态(t)          主页 → 更多 → 课程表（保证为空状态）
  goto_图库导入_基本信息确认页(t)  空状态 → 图库导入完整链路 → 确认课程表基本信息页
  图库导入_选图到确认页(t)      入口之后的共享段：提示弹窗→权限→视觉选图→解析→确认页
  goto_手动创建课程表(t)          课程表空状态 → 手动创建页
  tap_more_menu(t)              点顶栏「更多」并确认菜单弹出
  tap_rightmost_icon(t)         顶栏最右图标坐标（识别结果页禁用）
  top_bar_icons(t)              顶栏图标列表（调试辅助）

前置状态检查（2026-09-14）：每个 goto_* 带 skip_if_ready 开关，**默认 False
  （保持既有语义）**。传 True 时：已在目标页 → 直接返回，不冷启动、不重建前置
  （重跑调试省掉 pm_clear + 引导弹窗链，实测约 100s）；在上一跳 → 只走最后一跳
  （由内层 goto_* 的检查自然完成）。
  ⚠️ 跳过的是「冷启动 + 重建」，**不重置页内已被改动的数据** —— 断言默认值的
  用例（178 的「默认 50 分钟」、179 的时间默认值）必须保持 False，否则重跑会拿
  上一轮改过的值当默认值，断言变成间歇性 FAIL。

权限测试（169 系列共享；拆独立用例避免 USER_FIXED 级联）：
  navigate_to_course_table(t)   启动 App + 过首启弹窗 + 到课程表空状态页
  test_camera(t, allow)         相机权限 允许/拒绝 单行为测试
  test_gallery(t, allow)        图库权限 允许/拒绝 单行为测试
"""
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# test_framework 在 <skill包>/framework（本文件在 cases/<包名>/ 下），需显式加入路径
_FW = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework")
if _FW not in sys.path:
    sys.path.insert(0, _FW)

# TestCase 仅作类型提示；流程函数接受任何实现了框架 API 的对象
try:
    from test_framework import TestCase  # noqa: F401
except ImportError:  # 框架不可用时仍允许本模块被导入检查
    TestCase = object

PKG = "com.zui.calendar"

# 前置状态检查的全局开关（调试循环用）：DSH_SKIP_IF_READY=1 时，未显式传
# skip_if_ready 的 goto_* 调用会自动跳过「已在目标页」的前置。
# 正式回归不带该变量 → 语义与历史完全一致。
# 为什么是环境变量而不是逐用例改代码：是否可跳过取决于**运行期**「设备当前停在
# 哪一页」，同一用例跑成功/跑失败时停的页不同 —— 它是运行期属性，不是用例的静态
# 属性，故开关应由"跑的人"给。详见各 goto_* 的 docstring 与 plan/smart-skill-roadmap-v5.md A4.1。
_SKIP_IF_READY = os.environ.get("DSH_SKIP_IF_READY") == "1"

# 图库导入用到的素材与控件
IMG_PATH = "/sdcard/Pictures/日历/课程表.png"
BTN_GALLERY = "com.zui.calendar:id/btnImportFromGallery"
CROP_DONE = "com.zui.calendar:id/btnDone"
BTN_NEXT = "com.zui.calendar:id/btn_next"
PHOTO_THUMB = "com.android.providers.media.module:id/icon_thumbnail"

# 确认课程表基本信息页控件
PAGE_CONFIRM = "确认课程表基本信息"
NAME_RID = "com.zui.calendar:id/et_schedule_name"
BTN_FINISH = "com.zui.calendar:id/btn_finish"
LAY_START = "com.zui.calendar:id/layout_semester_start_date"
TV_START = "com.zui.calendar:id/tv_semester_start_date"
LAY_CUR_WEEK = "com.zui.calendar:id/layout_current_week"
TV_CUR_WEEK = "com.zui.calendar:id/tv_current_week"
LAY_TOTAL = "com.zui.calendar:id/layout_total_weeks"
TV_TOTAL = "com.zui.calendar:id/tv_total_weeks"
SW_WEEKEND = "com.zui.calendar:id/switch_weekend_classes"
SW_NONCURRENT = "com.zui.calendar:id/switch_show_non_current_week"

# 手动创建页（注意：完成按钮是 save_view，不是 btn_finish）
BTN_CREATE_MANUALLY = "com.zui.calendar:id/btnCreateManually"
SAVE_VIEW = "com.zui.calendar:id/save_view"

# 主页顶栏「更多」按钮：无 content-desc，必须用 resource-id 定位
# （靠"最右侧图标"猜测的旧方案已废：不同页面顶栏图标数量不同，会点错）
RID_MORE = "com.zui.calendar:id/iv_more"


def top_bar_icons(t):
    """日历顶栏图标列表 [(cx,cy,rid,desc), ...] 按 x 排序（y∈[100,450] 可点击无文字节点）。
    通用调试辅助；用例里定位具体按钮请优先 tap_rid/tap_text。"""
    items = []
    for n in t.find_nodes(clickable=True):
        b = n.get("bounds_xy")
        if not b or not (100 <= b[1] <= 450):
            continue
        if n["text"]:
            continue
        d = n["desc"]
        if "返回" in d or "上一层级" in d or "back" in d.lower():
            continue   # 排除返回箭头
        items.append(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, n["rid"], d))
    items.sort(key=lambda i: i[0])
    return items


def tap_more_menu(t, retries=8):
    """点主页顶栏「更多」并确认菜单弹出（出现'课程表'项）。
    元素定位（iv_more）+ 重试：等 App 就绪 + 抗转场抖动。成功返回 True。"""
    for _ in range(retries):
        b = t.el_bounds(rid=RID_MORE)
        if b:
            t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            time.sleep(1.2)
            if any("课程表" in x for x in t.screen_text()):
                return True
        time.sleep(1.5)
    return False


def tap_rightmost_icon(t):
    """顶栏最右侧图标（日历语义 = 「更多」）。优先 tap_more_menu()；
    识别结果页等无图标的页面会退化匹配到左侧返回键——不要在这类页面用。"""
    icons = top_bar_icons(t)
    return (icons[-1][0], icons[-1][1]) if icons else None


def _sleep(s=1.0):
    """settle 等待（非等 UI 元素）：固定值为真机调优结果，等待
    App 冷启动完成 / 页面转场 / PhotoPicker 渲染等无元素信号的 settle。
    等元素出现一律用 t.wait_rid / t.wait_text，不要往这里加 sleep。
    保留理由见 SKILL.md 注意事项（既有链路 sleep 属 settle 型）。"""
    time.sleep(s)


def _tap_rid_raw(t, rid):
    """按 resource-id 取 bounds 点击。
    PhotoPicker 属系统包，用原生 adb input 比 u2 更稳。
    """
    b = t.el_bounds(rid=rid)
    if b:
        t.adb_shell("input", "tap",
                    str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))
        return True
    return False


def _ensure_step(t, name):
    """防御：框架 record()/blocked() 直接解引用 _cur_step，未开 step 时会崩。

    本模块所有 goto_*/test_* 内部都会 record，隐含要求调用方先 t.step()。
    这个契约既没写进 docstring 也没有空值保护，新用例直接复用流程函数必崩
    （175 探查时实测踩到）。调用方应当自己开 step，这里只兜底。
    """
    if getattr(t, "_cur_step", None) is None:
        t.step(name)


def _tap_if_present(t, text, wait=3):
    """存在才点，不存在静默跳过。

    权限弹窗/提示框可能已被看门狗提前处理掉，此时 tap_text 找不到目标会记一条
    WARN，把环境噪音算进用例结论（175 探查实测到 '全部允许' 误报）。先判存再点；
    点击阶段也 silent：wait 命中后元素若被看门狗抢先点掉，tap 落空属于预期，
    不记 WARN，直接返回 False 交由外层逻辑兜底。
    """
    if t.wait_text(text, timeout=wait):
        return t.tap_text(text, wait=2, silent=True)
    return False


def restart_calendar(t, pm_clear=True):
    """冷启动日历并过掉首次引导弹窗。pm_clear=True 会清空数据（用例前置）。"""
    if pm_clear:
        t.pm_clear(PKG)
        _sleep(1.2)
    t.force_stop(PKG)
    _sleep(0.8)
    t.launch_app(PKG)
    time.sleep(4)    # settle：等冷启动首帧 + 首启权限弹窗（无 rid 可条件等待）
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=12, verbose=False)
    _dismiss_permission_guide(t)   # App 专属首启权限引导框（退出/同意），盖主页会挡住菜单
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=8, verbose=False)  # 同意后可能拉系统权限弹窗
    _sleep(1.5)
    return True


def _dismiss_permission_guide(t, timeout=8):
    """关掉 App 专属首启权限引导框「日历需要使用以下权限」（退出/同意）。

    该框是 App 自有 UI（非系统运行时权限弹窗），冷启动 pm_clear 后必现，
    会盖住主页导致 tap_more_menu 打不开菜单（174 实测 BLOCKED 入口）。
    框架 dismiss_first_use_dialogs 依赖 dialog_words 命中 同意 文本、且只在其
    轮询窗口内有效——引导框晚于该窗口出现就会漏掉。这里显式锚定标题处理更稳。
    点「同意」后 App 可能拉起系统运行时权限弹窗（允许/拒绝），交由随后的
    dismiss_first_use_dialogs 二次清扫；本函数只负责移除引导框本身。
    无引导框（如非 pm_clear 的重跑）在 2.5s 内未检出即提前退出，不空等。
    """
    start = time.time()
    while time.time() - start < timeout:
        txt = " ".join(t.screen_text())
        if "需要使用以下权限" in txt and "同意" in txt:
            if t.tap_text("同意", wait=2, silent=True):
                time.sleep(1.2)
                return True
            # 点不到再轮询一次（可能看门狗/其它机制抢先点掉）
        elif time.time() - start > 2.5:
            # 超过 2.5s 仍无引导框 → 本次冷启动未出现（如未 pm_clear），提前退出
            return False
        time.sleep(0.8)
    return False



def goto_课程表空状态(t, pm_clear=True, skip_if_ready=None):
    """主页 → 更多 → 课程表，保证停在空状态。返回是否成功。

    skip_if_ready=None（默认）：跟随环境变量 DSH_SKIP_IF_READY（未设置 = False，
    与历史语义完全一致）；显式传 True/False 时优先于环境变量。

    skip_if_ready=True 时：设备已停在空状态 → **直接返回**，不冷启动、不重建前置
    —— 重跑调试省掉整条冷启动链（实测 35.7s → 0.6s）。
    ⚠️ 跳过只保证「落在目标页」，**不重置页内已被改动的数据**：断言默认值 / 首启
    弹窗的调用方必须显式传 False（178 / 179 即属此类）。
    """
    if skip_if_ready is None:
        skip_if_ready = _SKIP_IF_READY
    _ensure_step(t, "前置-进入课程表空状态")
    if skip_if_ready and _on_课程表空状态(t):
        t.record("INFO", "已在课程表空状态，跳过冷启动+重建前置（skip_if_ready）")
        return True
    restart_calendar(t, pm_clear=pm_clear)
    if not tap_more_menu(t):
        t.blocked("无法打开'更多'菜单")
        return False
    _sleep(1.5)
    if not t.tap_text("课程表", wait=4, silent=True):
        t.blocked("未找到'课程表'入口")
        return False
    _sleep(3)
    if "还未添加课程表" not in " ".join(t.screen_text()):
        t.blocked("课程表非未添加课程表")
        return False
    return True


def goto_手动创建课程表(t, pm_clear=True, skip_if_ready=None):
    """课程表空状态 → 手动创建课程表页。

    skip_if_ready=None（默认）：跟随环境变量 DSH_SKIP_IF_READY（未设置 = False）。
    skip_if_ready=True 时：已在手动创建页（EditTimetableActivity）→ 直接返回；
    否则交由 goto_课程表空状态 的检查决定「只走最后一跳」还是「走完整前置」。
    ⚠️ 跳过不重置页内已被改动的数据，而 178/179 断言该页的「默认值」（课时长 /
    课间 / 节数 / 时间），重跑会把上轮改过的值当默认值 → FAIL。这两类必须 False。
    """
    if skip_if_ready is None:
        skip_if_ready = _SKIP_IF_READY
    if skip_if_ready and _on_手动创建页(t):
        t.record("INFO", "已在手动创建课程表页，跳过前置（skip_if_ready）")
        return True
    if not goto_课程表空状态(t, pm_clear=pm_clear, skip_if_ready=skip_if_ready):
        return False
    if not t.tap_rid(BTN_CREATE_MANUALLY, silent=True):
        t.blocked("未找到'手动创建课程表'按钮")
        return False
    _sleep(3)
    return True


def _on_确认页(t):
    """设备当前是否已停在「确认课程表基本信息」页（重跑跳过前置用）。"""
    try:
        if "确认课程表基本信息" in " ".join(t.screen_text()):
            return True
    except Exception:
        pass
    return False


def _on_课程表空状态(t):
    """设备当前是否已停在「课程表 - 还未添加课程表」空状态（重跑跳过前置用）。"""
    try:
        if "还未添加课程表" in " ".join(t.screen_text()):
            return True
    except Exception:
        pass
    return False


def _on_手动创建页(t):
    """设备当前是否已停在「手动创建课程表」页（新建课程表页 = EditTimetableActivity）。

    与 goto_手动创建课程表 的落点一致：点 btnCreateManually 后即进入该页。
    """
    try:
        return "EditTimetableActivity" in t.current_activity()
    except Exception:
        return False


# ── 图库选图（视觉排序 + 裁剪页预检 + 双态等待）────────────────────────
# 背景：PhotoPicker 照片 tab 按媒体库时间倒序，所有缩略图共用 icon_thumbnail
# 这个 rid，盲点"dump 第一张"会选中任何比素材新的图（其他用例拍照/截图都会
# 插队，媒体库是跨用例共享状态，pm_clear 不清 /sdcard）——172/175 曾因此稳定
# BLOCKED：选中桌面截图，App 弹「图片内容不是课程表」，而旧 flow 只认成功态
# 文本，烧满 60s 后错误归因为"超时，需联网"。
FAIL_DIALOG = "图片内容不是课程表"   # App 通用解析失败弹窗（模态，非 toast）：
#   选错图会弹、真课表云端解析偶发失败也弹（同素材 2 分钟后可成功，19:27/19:29 实测）
MAX_PICK_ATTEMPTS = 3                # 解析总次数上限（预检否决不计数）


def _visible_thumbs(t):
    """PhotoPicker 照片网格当前可见缩略图节点（dump 序 = 网格序 = 新→旧）。"""
    return t.find_nodes(rid_re=r"icon_thumbnail$")



# IME 感知的返回键与视觉排序评分已提升到框架层（通用能力，所有 App 适用）：
#   TestCase.ime_shown() / TestCase.back()          —— 框架 test_framework.py
#   TestCase.verdict_score() / TestCase.rank_by_vision()
# _flow 里保留薄封装，一是维持既有调用点不变，二是把日历专属的
# positive 特征词（星期表头/节次）集中在这里，不污染框架。
_SAFE_BACK = True   # 标记：本模块的 _safe_back 已委托框架层


def _ime_shown(t):
    """输入法是否弹起（委托框架层，保留此名以免改动历史调用点）。"""
    return t.ime_shown()


def _safe_back(t):
    """IME 感知的返回键（委托框架层 TestCase.back()）。"""
    return t.back()


def _verdict_score(t, ans):
    """视觉判词评分（委托框架层 t.verdict_score，带日历专属正向特征词）。

    正向特征词是**课程表**这一目标类型的知识，属于本 App 用例层职责；
    通用的"否定优先/模糊降权"逻辑在框架里。
    """
    return t.verdict_score(
        ans,
        positive=(("星期一", 6), ("星期二", 6), ("星期三", 6), ("星期四", 6),
                  ("星期五", 6), ("星期六", 6), ("星期日", 6), ("周末", 6),
                  ("星期表头", 6), ("时间段", 4), ("节次", 4)))


def _rank_thumbs(t, thumbs):
    """视觉排序缩略图（委托框架层 rank_by_vision + 日历正向特征词）。

    只依据通用结构特征（网格/星期表头/课程单元格），不依赖颜色风格——
    素材可能不止一种课表样式，素材特征写进提示词反而是干扰（讨论定稿）。
    返回 [(node, verdict), ...] 降序；视觉不可用时按保守分兜底。
    注意：缩略图小（~110px），本排序只决定尝试顺序；权威判定在裁剪页
    大图预检 + App 失败弹窗。
    """
    return t.rank_by_vision(
        thumbs,
        "这是一张手机图库的缩略图。它是否像一张'课程表'图片？"
        "只依据通用结构特征判断：表格/网格布局、顶部星期表头、"
        "单元格含课程名或时间段文字。不依赖颜色风格。"
        "回答格式：可能性(高/中/低)，加一句理由。",
        positive=(("星期一", 6), ("星期二", 6), ("星期三", 6), ("星期四", 6),
                  ("星期五", 6), ("星期六", 6), ("星期日", 6), ("周末", 6),
                  ("星期表头", 6), ("时间段", 4), ("节次", 4)))


def _ensure_grid(t, wait_s=12):
    """确保停在照片网格：已在网格直接成功；否则返回并条件等待。

    （失败弹窗点「知道了」后可能已自动回网格，此时再 BACK 会退出
    PhotoPicker——所以先查网格，查不到才 BACK。）

    183 实测（2026-09-10）：原来只按**一次** BACK，在输入法弹起时被吞掉，
    导致连续 3 次 BLOCKED「失败弹窗处理后未回到照片网格」。
    改为：IME 感知（t.back()）+ 最多 4 轮，每轮先确认是否已到网格（到位即停），
    既不会被 IME 吞掉，也不会因多按而退出 PhotoPicker。
    """
    deadline = time.time() + wait_s
    for _attempt in range(4):
        for _ in range(3):                      # 每轮先给自动回网格的机会
            if t.el_bounds(rid=PHOTO_THUMB):
                return True
            _sleep(0.8)
        if time.time() >= deadline:
            break
        t.back()                                 # IME 弹着会自动多按一次
        for _ in range(6):                       # 条件等待返回生效
            _sleep(1.0)
            if t.el_bounds(rid=PHOTO_THUMB):
                return True
    return False


def goto_图库导入_基本信息确认页(t, pm_clear=True, timeout=60, skip_if_ready=None):
    """完整图库导入链路 → 到达「确认课程表基本信息」页。

    对应 knowledge/com.zui.calendar.md 的「标准链路/图库导入创建课程表」。
    选图策略（2026-09-07 重构，勿回退成"盲点第一张"）：
      视觉排序候选（只按通用结构特征）→ 逐张：裁剪页大图预检（不是课表
      不消耗解析轮次）→ 通过才点「完成」触发解析 → 双态等待（成功页 /
      App「图片内容不是课程表」弹窗 / 弹窗被看门狗点掉后回网格）。
      预检否决 → 换下一候选；预检通过但解析被拒 → 重试同一张（App 对
      真课表也会偶发解析失败弹同一弹窗，同素材 2 分钟后可成功——弹窗是
      通用解析失败框，不是"选错图"专属）。解析总次数上限 MAX_PICK_ATTEMPTS，
      用尽 BLOCKED（归因"未找到可用课程表"，与网络无关）。

    skip_if_ready=None（默认）：跟随环境变量 DSH_SKIP_IF_READY（未设置 = False）。
    skip_if_ready=True：设备已在确认页时跳过 pm_clear + 导入直接返回 True，
    供失败重跑省前置；前提不满足自动回落完整导入。
    """
    if skip_if_ready is None:
        skip_if_ready = _SKIP_IF_READY
    if skip_if_ready and _on_确认页(t):
        t.record("INFO", "设备已在确认页，跳过 pm_clear+图库导入（skip_if_ready）")
        return True

    if not goto_课程表空状态(t, pm_clear=pm_clear, skip_if_ready=skip_if_ready):
        return False

    # 图片必须先被 MediaStore 收录，否则 PhotoPicker 显示"无相册"
    t.adb_shell("am", "broadcast", "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", "file:///sdcard/Pictures/日历/课程表.png")
    _sleep(2)

    if not t.tap_rid(BTN_GALLERY, silent=True):
        t.blocked("未找到'从图库导入课程表'按钮")
        return False
    _sleep(2)
    return 图库导入_选图到确认页(t, timeout=timeout)


def 图库导入_选图到确认页(t, timeout=60):
    """共享链路（170/172/175 共用）：提示弹窗 → 照片权限 → PhotoPicker
    视觉选图 → 裁剪预检 → 解析双态等待 → 「下一步」→ 确认课程表基本信息页。

    调用方需已点中导入入口（空状态按钮 btnImportFromGallery 或已有课表
    展示页菜单「图库导入课程表」均可），看门狗策略由调用方决定——本函数
    自带 0.5s 级双态轮询，不依赖看门狗也能接住失败弹窗。
    失败时本函数已按原因 record/blocked（归因准确），调用方直接 return
    即可，不要再叠加 FAIL（否则环境类 BLOCKED 会被升级成 FAIL）。
    """
    # 用本模块的 _dismiss_image_hint 而非裸 tap_text：它轮询等框出现，且能识别
    # "已被看门狗关掉 / 直接进了 PhotoPicker"从而跳过。裸 tap_text 在看门狗抢先
    # 关框时找不到'知道了'，会白白记一条 WARN（175 探查时实测踩到）。
    _dismiss_image_hint(t)
    _sleep(2.5)
    _tap_if_present(t, "全部允许", wait=4)   # 系统照片权限（可能已被看门狗点掉）
    _sleep(3.5)

    _tap_if_present(t, "照片", wait=6)      # PhotoPicker 切到照片 tab
    _sleep(3)
    thumbs = _visible_thumbs(t)
    if not thumbs:
        # PhotoPicker 首次冷启动渲染较慢，tab 切换可能落空（175 实测）。
        _tap_if_present(t, "照片", wait=5)
        _sleep(3)
        thumbs = _visible_thumbs(t)
    if not thumbs:
        t.blocked("PhotoPicker 无可选图片（缺素材或未被 MediaStore 收录）")
        return False

    ranked = _rank_thumbs(t, thumbs)
    t.record("INFO", "图库缩略图视觉排序（高→低）: "
             + " | ".join(f"候选{i+1}:{v[:36]}"
                          for i, (_, v) in enumerate(ranked)))

    parse_ok = False
    parse_tries = 0
    ci = 0                 # 候选下标：预检否决才前进；预检通过但解析被拒 → 重试同一张
    while ci < len(ranked) and parse_tries < MAX_PICK_ATTEMPTS:
        node, _verdict = ranked[ci]
        b = node["bounds_xy"]
        t.adb_shell("input", "tap",
                    str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))
        got_crop = False
        for _ in range(10):              # 条件等待裁剪页（替代固定 sleep）
            _sleep(1)
            if t.el_bounds(rid=CROP_DONE):
                got_crop = True
                break
        if not got_crop:
            t.blocked(f"点击候选{ci+1}缩略图后未进入裁剪页")
            return False

        # 裁剪页预检（便宜闸门）：大图判定，不是课表就不消耗解析轮次，
        # 也绕开「知道了」之后落点不确定的回头路
        try:
            crop_ans = t.vision_ask(
                "这是裁剪页，中间是被选中的大图。它是否是一张课程表？"
                "只依据结构特征（网格、星期表头、课程单元格）判断，"
                "不依赖颜色风格。回答以 是 或 否 开头。")
        except Exception as e:
            crop_ans = f"是（视觉不可用放行:{e}）"   # 视觉挂了不卡死链路
        t.screenshot(f"候选{ci+1}_裁剪页预检")
        # 判定用否定词匹配，不能用 startswith("否")：模型回答不保证"是/否"
        # 开头，"不是课程表，这是桌面截图" 这类回答会漏判（19:27 验证实测）
        if re.search(r"不是|并非", crop_ans) or (crop_ans or "").startswith(("否", "不")):
            t.record("INFO",
                     f"候选{ci+1} 预检非课程表: {crop_ans[:60]} → 换下一张")
            if not _ensure_grid(t):
                t.blocked("裁剪页返回后未回到照片网格")
                return False
            ci += 1
            continue

        if not _tap_rid_raw(t, CROP_DONE):   # 预检通过 → 触发解析
            t.blocked("未进入裁剪页")
            return False
        parse_tries += 1

        # 双态等待（时间 deadline，dump 本身 ~1s/轮即天然限速）：
        #   成功页「确认识别结果」 / App 失败弹窗「图片内容不是课程表」 /
        #   弹窗被看门狗抢先点掉后回到网格（三等价失败信号，抗竞争）
        state = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            txt = " ".join(t.screen_text())
            if "确认识别结果" in txt:
                state = "ok"
                break
            if FAIL_DIALOG in txt:
                state = "rejected"
                break
            if t.el_bounds(rid=PHOTO_THUMB):
                state = "rejected"
                break
        if state == "ok":
            parse_ok = True
            break
        if state == "rejected":
            t.screenshot(f"候选{ci+1}_被判定非课程表")
            t.record("INFO",
                     f"候选{ci+1} 触发「{FAIL_DIALOG}」弹窗"
                     f"（第{parse_tries}/{MAX_PICK_ATTEMPTS}次解析）")
            # 必须先点「知道了」关弹窗：弹窗不关，BACK 只关弹层回不到网格
            #（19:27 验证实测 BLOCKED）。弹窗可能已被看门狗点掉，落空属预期
            t.tap_text("知道了", wait=2, silent=True)
            if not _ensure_grid(t):
                t.blocked("失败弹窗处理后未回到照片网格")
                return False
            # 走到这里说明预检已认定是课表 → App 拒绝多为云端解析偶发失败
            #（同素材 2 分钟后解析成功的实测），重试同一张不换候选
            if parse_tries < MAX_PICK_ATTEMPTS:
                t.record("INFO", "预检已判定为课程表 → 重试同一张（解析偶发失败）")
            continue
        t.record("INFO", f"候选{ci+1} 解析 {timeout}s 无成功/失败信号")
        if _ensure_grid(t):
            continue
        t.blocked(f"图片解析未完成（{timeout}s 无响应且无法返回照片网格，需联网）")
        return False

    if not parse_ok:
        t.blocked(f"图库未找到可用课程表：非课程表候选已被预检剔除；"
                  f"课程表候选解析 {parse_tries} 次均被 App 拒绝"
                  f"（「{FAIL_DIALOG}」，含云端解析偶发失败可能）")
        return False
    t.screenshot("确认识别结果")
    _sleep(1)

    if not _tap_rid_raw(t, BTN_NEXT):
        t.blocked("未找到'下一步'")
        return False
    _sleep(3)
    if PAGE_CONFIRM not in " ".join(t.screen_text()):
        t.blocked("未进入'确认课程表基本信息'页")
        return False
    t.screenshot("基本信息确认页")
    return True


# ── 权限测试共享辅助（原 _perm_helper.py，169 系列用例共用）──────────────
# 拆分原因：Android 权限拒绝后 USER_FIXED，同轮多次测会级联失败；
# 每个行为独立干净环境最可靠。


def navigate_to_course_table(t):
    """启动 App + 处理首启弹窗 + 导航到课程表空状态页"""
    # 确保干净前台：杀掉日历和相机（上个用例可能停在相机）。
    # 一律走 t.force_stop/t.launch_app：绑定用例 serial，多设备不串台
    t.force_stop(PKG)
    t.force_stop("com.zui.camera")
    time.sleep(1)    # settle：等进程退出（非 UI 信号，无法条件等待）
    t.launch_app(PKG)
    # 等 App 就绪（有弹窗或有主界面工具栏），最多 10s
    for _ in range(10):
        texts = t.screen_text()
        if any(w in " ".join(texts) for w in ("同意", "允许", "我知道了", "知道了")):
            break
        if tap_rightmost_icon(t):
            break
        time.sleep(1)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=15, verbose=True)
    if not tap_more_menu(t, retries=10):
        print("[导航] 更多菜单未弹出（无'课程表'项）, 屏幕:", t.screen_text()[:6])
        return False
    t.tap_text("课程表", silent=True)
    for _ in range(10):
        if any("拍照导入" in x for x in t.screen_text()):
            return True
        time.sleep(0.6)
    return False


def _dismiss_image_hint(t, timeout=8):
    """关掉「请确保图片清晰、完整」提示框（点'知道了'）。

    这个框会挡住后续动作：不点掉，相机/相册永远不会被拉起
    （实测点击拍照导入后 25s 相机仍未打开，直至手动关框）。
    看门狗虽会兜底，但存在时序竞争（169 主用例赢过、独立用例输过），
    所以在权限测试里显式等待并关闭，不等看门狗。
    """
    for _ in range(int(timeout / 0.5)):
        texts = t.screen_text()
        if any("知道了" in x for x in texts):
            # silent：screen_text 命中与点击之间看门狗可能抢先关框，tap 落空
            # 属预期（框已不在），不记 WARN，照常按"框已关"返回 True。
            t.tap_text("知道了", wait=2, silent=True)
            time.sleep(0.6)
            return True
        # 框没出现（或已消失）→ 可能直接进了系统权限弹窗/相机
        act = t.current_activity()
        if any(k in act.lower() for k in ("camera", "photopicker", "permission")):
            return False
        time.sleep(0.5)
    return False


def test_camera(t, allow):
    """相机权限: allow=True 期望相机打开; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("拍照导入课程表", silent=True)   # 失败由下方 Activity/文案断言兜底
    _dismiss_image_hint(t)          # 挡路框必须先关，否则相机不会被拉起
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "camera" in act.lower():
                break
        ok = "camera" in act.lower() or "zui.camera" in act.lower()
        t.record("PASS" if ok else "FAIL",
                 f"允许后相机打开: {act}")
        t.screenshot("相机_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("相机权限" in x for x in texts) or any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相机权限: {texts[:5]}")
        t.screenshot("相机_拒绝")


def test_gallery(t, allow):
    """图库权限: allow=True 期望进入照片选择; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("从图库导入课程表", silent=True)  # 失败由下方 Activity/文案断言兜底
    _dismiss_image_hint(t)          # 挡路框必须先关，否则相册不会被拉起
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "photopicker" in act.lower():
                break
        ok = "photopicker" in act.lower() or "PhotoPicker" in act
        t.record("PASS" if ok else "FAIL",
                 f"允许后进入照片选择界面: {act}")
        t.screenshot("图库_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("权限" in x for x in texts) and any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相册权限: {texts[:5]}")
        t.screenshot("图库_拒绝")


def goto_home(t, rounds=5):
    """把日历带回**主页**（`iv_more` 所在页）。返回是否到达。

    为什么需要：185/186 原来只 `launch_app` 就假设"落在主页"，但套件里上一个
    用例可能把它停在深层页（时间设置页 / 编辑页 / 周视图）→ 找不到 `iv_more`
    → FAIL+BLOCKED（实测：185/186 在全量套件里 20 秒就结束，报告写"未找到首页
    「更多」按钮"）。`launch_app` 是**恢复**前台，不是回主页。

    路径（知识卡）：先关可能盖着的弹窗，再逐次 BACK —— 列表页 BACK 一次到周视图，
    再 BACK 一次到日历主页。
    """
    t.observe_dialogs(rounds=2)
    for _ in range(rounds):
        if t.el_bounds(rid="com.zui.calendar:id/iv_more"):
            return True
        t.back()
        time.sleep(1.1)
        t.observe_dialogs(rounds=1)
    return bool(t.el_bounds(rid="com.zui.calendar:id/iv_more"))


# ── 备数据：确保 ≥2 个课程表（185/186 前置）──────────────────────
RID_CREATE_MANUALLY = "com.zui.calendar:id/btnCreateManually"
RID_ADD_SCHEDULE = "com.zui.calendar:id/action_add_schedule"
RID_ET_TABLE_NAME = "com.zui.calendar:id/et_schedule_name"
RID_TABLE_SETTINGS = "com.zui.calendar:id/action_curriculum_table_settings"
RID_EMPTY_VIEW = "com.zui.calendar:id/emptyView"
RID_SAVE_VIEW = "com.zui.calendar:id/save_view"


def table_names(t):
    """当前页上的课程表名（不在列表页 → 空列表）。"""
    return [n["text"] for n in t.find_nodes(rid_re="tv_schedule_name") if n["text"]]


def ensure_two_tables(t, want=2, max_new=3):
    """确保设备上有 ≥want 个课程表（185/186 的前置数据）。返回课程表名列表。

    为什么必须自己补建：这两个用例的前提是"设备中已添加多个课程表"，而套件里
    前序用例会 `pm_clear` → 进课程表落的是**引导式空状态页**（「还未添加课程表」），
    那页**没有**列表页顶栏的加号 `action_add_schedule`。用例原来先去找列表页
    → 必然 FAIL+BLOCKED（实测：185/186 放进全量套件后 20 秒就结束）。

    三种落点分别处理 —— 且**建完的落点是周视图**（知识卡：点「完成」保存后到
    TimetableActivity，不是列表页），所以每一轮都要重新导航，不能按次数连建：
      · 空状态页（`emptyView` 在）→ `btnCreateManually`（该页唯一入口）
      · 列表页 → `action_add_schedule`
      · 周视图 → 顶栏 `action_curriculum_table_settings` 回列表页
    """
    for _ in range(max_new + 1):
        names = table_names(t)
        if len(names) >= want:
            return names
        if not t.tap_rid(RID_TABLE_SETTINGS, silent=True):
            t.el_bounds(rid=RID_EMPTY_VIEW)          # 空状态页：原地即可
        time.sleep(2)
        entry = (RID_CREATE_MANUALLY
                 if t.el_bounds(rid=RID_CREATE_MANUALLY) else RID_ADD_SCHEDULE)
        if not t.tap_rid(entry, silent=True):
            return table_names(t)                    # 进不去新建页 → 交用例报 BLOCKED
        time.sleep(3)
        if not t.el_bounds(rid=RID_ET_TABLE_NAME):
            return table_names(t)
        # 表名必须**全局唯一**：用 len(names) 编号会在"列表读取滞后/上次残留"
        # 时造出重名表 → 用例按名字区分"当前/非当前"时判错（实测 186 因此
        # others 判空、185 的置灰断言连挂 7 条）。用时间戳保证唯一。
        t.input_text(RID_ET_TABLE_NAME, f"备数据表{int(time.time()) % 100000}")
        # 保存 = toolbar 的 save_view（文本「完成」），不是 btn_finish
        if not (t.tap_rid(RID_SAVE_VIEW, silent=True)
                or t.tap_text("完成", wait=2, silent=True)):
            return table_names(t)
        time.sleep(3)
        t.observe_dialogs(rounds=2)
    return table_names(t)


# ── 弹框滚轮通用工具（178 实战沉淀）───────────────────────────
def _parse_panel(t):
    """读弹框 customPanel bounds。"""
    from test_framework import _parse_nodes
    for n in _parse_nodes(t._dump()):
        if n["rid"] == "com.zui.calendar:id/customPanel" and n["bounds_xy"]:
            return n["bounds_xy"]
    return None


def wheel_tap_steps(t, col_x, n, up=True, panel=None):
    """弹框滚轮按档位次数点按（不读 OCR 反馈）。

    坑（178 实测踩过）：弹框滚轮是 Canvas 自绘，OCR 在弹框内偶发漏读；
    滚轮又是循环的，漏读会让脚本以为"没动"继续点 → 在循环上绕圈。
    故拨值不应让 OCR 反馈参与循环，按确定次数点按更稳。

    约定方向：上=减小、下=增大；每档 ≈ 5 分钟（时长/课间）。
    panel=(x1,y1,x2,y2)，缺省时现场从当前弹框取。

    ⚠️ **点按会偶发丢档**（轮子在惯性/动画未 settle 时被吃掉）：实测 178 的
    "拨到上端 + 越界回绕" 5 次跑出 **2 通过 3 失败**，根因是前一步少拨一档 →
    后一步的回绕断言跟着假 FAIL。所以：① 档间延时放宽到 0.9s；
    ② **多步拨动后不要直接断言"下一步应该到 X"** —— 弹框内读不到 Canvas 文字，
    必须点确定后从 value_rid 读回核对，不到位就补拨。
    """
    if panel is None:
        panel = _parse_panel(t)
    cy_line = (panel[1] + panel[3]) // 2
    step_px = int((panel[3] - panel[1]) / 3)
    for _ in range(n):
        t.tap_xy(col_x, cy_line - step_px if up else cy_line + step_px, observe=False)
        time.sleep(0.9)


def apply_and_read(t, value_rid):
    """点弹框「确定」→ 若弹出「是否根据课程时长和休息时长自动调整其他课程？」
    二次确认框也点「确定」→ 返回 value_rid 的当前文本（如 '5分钟'）。
    自动跳过场景：若仅需点取消，请直接 t.tap_text('取消', ...) 后自行读取。"""
    if t.tap_text("确定", wait=4, silent=True):
        time.sleep(2.5)
        t.observe_dialogs(rounds=3)
        if "是否根据课程时长和休息时长自动调整其他课程" in " ".join(t.screen_text()):
            t.tap_text("确定", wait=3, silent=True)
            time.sleep(2)
            t.observe_dialogs(rounds=3)
    time.sleep(1.0)
    return (t.read_rid(value_rid) or {}).get("text", "")
