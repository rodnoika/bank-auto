$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$webRoot = Join-Path $projectRoot "web"
$backendLog = Join-Path $projectRoot ".local-backend.log"
$backendErrorLog = Join-Path $projectRoot ".local-backend-error.log"

$backend = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000", "--env-file", ".env" `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErrorLog `
    -PassThru

try {
    Set-Location $webRoot
    npm run dev
}
finally {
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
}
