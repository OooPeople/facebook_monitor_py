$ErrorActionPreference = "Stop"

# 專案限定 uv wrapper：固定從專案根目錄執行，並使用工作區內的 cache。
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv-cache"

if ($args.Count -eq 0) {
    Write-Host "Usage: .\scripts\uv.ps1 sync"
    Write-Host "Usage: .\scripts\uv.ps1 run playwright install chromium"
    Write-Host "Usage: .\scripts\uv.ps1 run facebook-monitor"
    Write-Host "Usage: .\scripts\uv.ps1 run facebook-monitor-login"
    exit 2
}

Push-Location $ProjectRoot
try {
    & uv @args
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
