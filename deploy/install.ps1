# ══════════════════════════════════════════════════════════════════
# K.H.A.V.I.S. Windows 一鍵部署安裝器（PowerShell 5.1+ / Windows 10+）
#
# 使用方式（在 repo 根目錄的 PowerShell 中執行）：
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\deploy\install.ps1
#   .\deploy\install.ps1 -Home "$env:USERPROFILE\khavis" -Port 8080
#   .\deploy\install.ps1 -SkipClone      # 只更新依賴與設定
#
# 功能：檢查環境 → clone repo → venv + 依賴 → .env 初始化（不覆蓋）
#       → （選配）登錄開機排程 → 健康檢查。
#
# 注意：K.A.V.I.S. 遠端派工橋接器（hermes adapter）僅支援 Unix 環境，
#       Windows 上 K.A.V.I.S. 以本地 LangGraph 模式運行。
# ══════════════════════════════════════════════════════════════════
[CmdletBinding()]
param(
    [Alias("Home")]
    [string]$InstallHome = (Join-Path $env:USERPROFILE "khavis"),
    [int]$Port = 8080,
    [string]$Repo = "https://github.com/kiddhsu5/KHAVIS.git",
    [string]$Branch = "main",
    [switch]$SkipClone,
    [switch]$NoService
)

$ErrorActionPreference = "Stop"
function Log($m)  { Write-Host "[install] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

# ── 1. 環境檢查 ──────────────────────────────────────────────────
Log "檢查環境…"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "需要 Git（https://git-scm.com/download/win）" }

$py = $null
foreach ($cand in @("py", "python3", "python")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            if ($cand -eq "py") { $ver = & $cand -3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
            else { $ver = & $cand -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
            if ($ver) {
                $parts = $ver.Split(".")
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { $py = $cand; break }
            }
        } catch { }
    }
}
if (-not $py) { Fail "需要 Python ≥ 3.11（https://www.python.org/downloads/windows/，安裝時勾選 Add to PATH）" }
$pyVer = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
Log "Python $pyVer ($py) ✓  git ✓"

# ── 2. 取得專案 ──────────────────────────────────────────────────
if ($SkipClone) {
    Log "略過 clone（-SkipClone），使用既有 $InstallHome"
    if (-not (Test-Path $InstallHome)) { Fail "-SkipClone 需要已存在的 $InstallHome" }
} elseif (Test-Path (Join-Path $InstallHome ".git")) {
    Log "$InstallHome 已是 git repo → git pull 更新"
    & git -C $InstallHome fetch origin $Branch
    & git -C $InstallHome reset --hard "origin/$Branch"
} else {
    if (Test-Path $InstallHome) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$InstallHome-preinstall-$stamp"
        Warn "$InstallHome 已存在且不是 git repo → 搬到 $backup（資料不會遺失）"
        Move-Item -LiteralPath $InstallHome -Destination $backup
        Log "稍後請從 $backup 回復：.env、config/workbench_catalog.json、workspace/、logs/"
    }
    Log "Clone $Repo ($Branch) → $InstallHome"
    & git clone --depth 1 -b $Branch $Repo $InstallHome
    if ($LASTEXITCODE -ne 0) { Fail "git clone 失敗" }
}

# ── 3. venv + 依賴 ───────────────────────────────────────────────
Log "建立 venv 並安裝依賴…"
& $py -m venv (Join-Path $InstallHome ".venv")
$pip = Join-Path $InstallHome ".venv\Scripts\pip.exe"
& $pip install --upgrade pip --quiet
& $pip install -r (Join-Path $InstallHome "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "依賴安裝失敗" }
Log "依賴安裝完成"

# ── 4. .env 初始化（不覆蓋）─────────────────────────────────────
$envFile = Join-Path $InstallHome ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $InstallHome ".env.example") $envFile
    Log "已從 .env.example 建立 .env — 請填入供應商 API keys"
} else {
    Log ".env 已存在，保留不動"
}

# ── 5. 啟動與（選配）排程 ────────────────────────────────────────
$pythonExe = Join-Path $InstallHome ".venv\Scripts\python.exe"
$serverPy = Join-Path $InstallHome "scripts\status_server.py"

if (-not $NoService) {
    $taskName = "KHAVIS-Workbench"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        try {
            $action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$serverPy`" --port $Port" -WorkingDirectory $InstallHome
            $trigger = New-ScheduledTaskTrigger -AtLogOn
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "K.H.A.V.I.S. Workbench (port $Port)" -Force | Out-Null
            Log "已登錄開機排程：$taskName"
        } catch {
            Warn "排程登錄失敗（可能權限不足）→ 之後手動啟動：$pythonExe `"$serverPy`" --port $Port"
        }
    }
    Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

# ── 6. 健康檢查 ──────────────────────────────────────────────────
if ($NoService) {
    Log "背景啟動做健康檢查（NoService 模式之後請手動啟動）"
    Start-Process -FilePath $pythonExe -ArgumentList "`"$serverPy`" --port $Port" -WorkingDirectory $InstallHome -WindowStyle Hidden
}
Start-Sleep -Seconds 4
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 6
    if ($resp.StatusCode -eq 200) { Log "健康檢查 ✓ http://127.0.0.1:$Port/healthz" }
    else { Fail "健康檢查失敗（HTTP $($resp.StatusCode)）" }
} catch {
    Fail "健康檢查失敗：$($_.Exception.Message)。請檢查 $serverPy 日誌"
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host " K.H.A.V.I.S. 安裝完成"
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host " 專案目錄   : $InstallHome"
Write-Host " Workbench  : http://127.0.0.1:$Port/workbench"
Write-Host " 內建 agent : K.A.V.I.S.（本地 LangGraph 7 角色團隊協同）"
Write-Host " 升級方式   : git -C $InstallHome pull 後重新啟動"
Write-Host ""
Write-Host " 後續手動步驟："
Write-Host " 1. 編輯 $envFile 填入供應商 API keys"
Write-Host " 2. 手動啟動：$pythonExe `"$serverPy`" --port $Port"
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
