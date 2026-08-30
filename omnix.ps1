param([switch]$Cli, [switch]$Desktop)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Virtual environment not found. Creating it now (one-time, may take a few minutes)..."
    python -m venv (Join-Path $root ".venv")
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r (Join-Path $root "requirements.txt") --quiet
}

Push-Location $root
try {
    if ($Cli) {
        & $venvPython -m omnix.cli
        return
    }

    # Always re-wire the frontend from the design export so it's current.
    if (Test-Path (Join-Path $root "OMNIX.html")) {
        Write-Host "Wiring frontend from OMNIX.html ..."
        & $venvPython (Join-Path $root "build_frontend.py")
    }

    if ($Desktop) {
        # Native OMNIX-BugHound desktop app (PyWebview). The window IS the UI, so
        # no browser is opened. The server runs inside omnix.desktop's own thread.
        $stuck = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($stuck) {
            Write-Host "Port 8000 is busy - stopping the old server..."
            $stuck.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Milliseconds 700
        }
        Write-Host ""
        Write-Host "Launching OMNIX-BugHound (native window)..." -ForegroundColor Cyan
        Write-Host "Close the window to quit." -ForegroundColor Cyan
        & $venvPython -m omnix.desktop
        return
    }

    # If an old server is stuck on port 8000, stop it first.
    $stuck = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($stuck) {
        Write-Host "Port 8000 is busy - stopping the old server..."
        $stuck.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 700
    }

    # A background watcher opens the browser only once the server is actually
    # answering, so you never land on a "can't connect" page.
    $opener = Start-Job {
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                # 10s, not 2s: when Ollama is down the health probe used to take
                # ~2.2s to fail, so every poll timed out and the browser never
                # opened. Health is fast again now, but the margin costs nothing.
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 10
                if ($r.StatusCode -eq 200) {
                    $h = $r.Content | ConvertFrom-Json
                    # On the cloud path Ollama is irrelevant — don't nag about it.
                    if ($h.backend -eq "cloud") {
                        Start-Process "http://127.0.0.1:8000"
                        break
                    }
                    if (-not $h.ollama) {
                        Write-Host ""
                        Write-Host "  NOTE: Ollama is not reachable. Open another terminal and run:  ollama serve" -ForegroundColor Yellow
                    } elseif ($h.missing -and $h.missing.Count -gt 0) {
                        Write-Host ""
                        Write-Host ("  NOTE: These models are not pulled yet: " + ($h.missing -join ", ")) -ForegroundColor Yellow
                        Write-Host "  Pull the main one with:  ollama pull llama3.2:3b" -ForegroundColor Yellow
                    }
                    Start-Process "http://127.0.0.1:8000"
                    break
                }
            } catch { }
        }
    }

    Write-Host ""
    Write-Host "OMNIX server starting at http://127.0.0.1:8000" -ForegroundColor Cyan
    Write-Host "The browser opens automatically once it's ready." -ForegroundColor Cyan
    Write-Host "If a tab is already open, hard-refresh it with Ctrl+Shift+R." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C here to stop the server."
    Write-Host ""

    # Run the server in the foreground (Ctrl+C stops it cleanly).
    & $venvPython -m omnix.server
}
finally {
    if ($opener) { Remove-Job $opener -Force -ErrorAction SilentlyContinue }
    Pop-Location
}
