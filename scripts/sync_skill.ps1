#!/usr/bin/env pwsh
# 把 skill 包（单一数据源）同步到工作区，或反向同步改动过的代码文件。
#
#   pwsh -File sync_skill.ps1           # 默认：skill 包 → 工作区（新机器/拉取他人改动后）
#   pwsh -File sync_skill.ps1 -ToSkill  # 工作区 → skill 包（改完代码后提交分享）
#
# 同步范围：
#   framework/*  —— 代码（webui.py/.html/.js/.css、test_framework.py、run_case.py…）
#   tests/*.py   —— 单元测试（否则工作区 tests/ 是空目录，跑单测会报 Start directory
#                   is not importable）
#   scripts/ 下的脚本  —— run_case.ps1 / setup.* / webui.* / export.sh / sync_skill.ps1
#   SKILL.md     —— 技能说明（工作区与 skill 包保持一致）
#
# 不同步（内容/运行产物，各居其位）：
#   cases/ knowledge/  → 首次 setup 时复制到工作区，之后工作区是单一数据源
#   storage/（含 test_records.db） .venv/ → 只在工作区（运行产物，机器私有）

param([switch]$ToSkill)

$ErrorActionPreference = 'Stop'
$SkillDir = Split-Path -Parent $PSScriptRoot
$Workspace = if ($env:DSH_WORKSPACE_DIR) { $env:DSH_WORKSPACE_DIR }
             else { Join-Path $HOME 'android-test-skills-data' }

if (-not (Test-Path -LiteralPath $Workspace)) {
    Write-Host "工作区不存在: $Workspace" -ForegroundColor Red
    Write-Host "请设置 DSH_WORKSPACE_DIR 或先运行 setup.ps1" -ForegroundColor Yellow
    exit 1
}

if ($ToSkill) { $from, $to, $dir = $Workspace, $SkillDir, '工作区 → skill 包' }
else          { $from, $to, $dir = $SkillDir, $Workspace, 'skill 包 → 工作区' }

Write-Host "同步方向: $dir" -ForegroundColor Cyan
Write-Host "  源: $from"
Write-Host "  目标: $to"
Write-Host ""

$files = @()
# framework 下的代码文件（包含 preparation 等子包，排除 Python 缓存）
$frameworkSource = Join-Path $from 'framework'
Get-ChildItem $frameworkSource -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc', '.pyo') } |
    ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($frameworkSource, $_.FullName)
        $files += Join-Path 'framework' $relative
    }
# 单元测试
Get-ChildItem (Join-Path $from 'tests') -File -Filter '*.py' -ErrorAction SilentlyContinue | ForEach-Object {
    $files += Join-Path 'tests' $_.Name
}
foreach ($n in @('SKILL.md')) {
    if (Test-Path -LiteralPath (Join-Path $from $n)) { $files += $n }
}
# scripts/ **全量**。原来是 7 个文件的白名单 —— 实测漏掉 check_bom.ps1 /
# check_context_budget.py / cleanup_records.py，后果是：工作区跑不了 SKILL.md
# 硬要求的 check_bom，也跑不了依赖它们的单测（`tests/` 会 ModuleNotFoundError）。
Get-ChildItem (Join-Path $from 'scripts') -File -ErrorAction SilentlyContinue | ForEach-Object {
    $files += Join-Path 'scripts' $_.Name
}
# evals/ **全量**（静态 lint + 事实守门；`tests/test_evals.py` 依赖它们）。
# 不同步 → 工作区的 tests/ 同样跑不出完整单测。
Get-ChildItem (Join-Path $from 'evals') -File -Filter '*.py' -ErrorAction SilentlyContinue | ForEach-Object {
    $files += Join-Path 'evals' $_.Name
}

$copied = 0; $skipped = 0; $failed = 0
foreach ($rel in $files) {
    $src = Join-Path $from $rel
    $dst = Join-Path $to $rel
    if (-not (Test-Path -LiteralPath $src)) { continue }
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }

    # 内容相同则跳过（避免无谓改动时间戳）
    if (Test-Path -LiteralPath $dst) {
        $h1 = (Get-FileHash -LiteralPath $src -Algorithm MD5).Hash
        $h2 = (Get-FileHash -LiteralPath $dst -Algorithm MD5).Hash
        if ($h1 -eq $h2) { $skipped++; continue }
    }
    try {
        Copy-Item -LiteralPath $src -Destination $dst -Force
        Write-Host "  ✓ $rel" -ForegroundColor Green
        $copied++
    } catch {
        Write-Host "  ✗ $rel : $_" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
Write-Host "完成：复制 $copied，已是最新 $skipped，失败 $failed" -ForegroundColor Cyan

# 校验：BOM（PowerShell 5.1 解析 UTF-8 脚本必需）
$bad = @()
foreach ($rel in ($files | Where-Object { $_ -like '*.ps1' })) {
    $p = Join-Path $to $rel
    if (-not (Test-Path -LiteralPath $p)) { continue }
    $b = [System.IO.File]::ReadAllBytes($p)
    if ($b.Length -lt 3 -or $b[0] -ne 0xEF -or $b[1] -ne 0xBB -or $b[2] -ne 0xBF) { $bad += $rel }
}
if ($bad) {
    Write-Host "警告：以下 .ps1 缺少 UTF-8 BOM，PowerShell 5.1 会按 GBK 解析导致乱码：" -ForegroundColor Yellow
    $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
} else {
    Write-Host "校验：所有 .ps1 均带 UTF-8 BOM" -ForegroundColor Green
}

if (-not $ToSkill) {
    Write-Host ""
    Write-Host "提示：同步后需重启 Web UI 才生效：" -ForegroundColor DarkGray
    Write-Host "  pwsh -File (Join-Path '$SkillDir' 'scripts\webui.ps1') stop" -ForegroundColor DarkGray
    Write-Host "  pwsh -File (Join-Path '$SkillDir' 'scripts\webui.ps1') start" -ForegroundColor DarkGray
}
