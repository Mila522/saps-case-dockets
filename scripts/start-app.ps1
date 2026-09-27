$ErrorActionPreference = 'Stop'
$sapsRoot = Split-Path -Parent $PSScriptRoot
$sapsPython = Join-Path $sapsRoot '.venv\Scripts\python.exe'
$sapsUrl = 'http://127.0.0.1:8000'

function Test-SapsServer {
    try {
        $response = Invoke-RestMethod "$sapsUrl/" -TimeoutSec 2
        return $response.message -eq 'SAPS Case-Docket Management API'
    } catch { return $false }
}

try {
    if (-not (Test-Path -LiteralPath $sapsPython)) {
        throw 'The project Python environment is missing. Restore .venv before launching.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sapsRoot '.env'))) {
        throw 'The project .env configuration is missing. Complete local setup first.'
    }
    if (-not (Test-SapsServer)) {
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($listener) { throw 'Port 8000 is used by another application. Close it before starting SAPS.' }
        Write-Host 'Starting SAPS...'
        $server = Start-Process -FilePath $sapsPython -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $sapsRoot -WindowStyle Hidden -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if (Test-SapsServer) { $ready = $true; break }
            $server.Refresh()
            if ($server.HasExited) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'SAPS could not start. Check the project configuration and PostgreSQL service.' }
    } else { Write-Host 'SAPS is already running.' }

    # Check the executable as well as the module to avoid starting duplicate workers.
    $workers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
        $_.ExecutablePath -eq $sapsPython -and
        $_.CommandLine -match '-m\s+app\.modules\.communications\.case_email\s+--watch'
    })
    if ($workers.Count -eq 0) {
        $worker = Start-Process -FilePath $sapsPython -ArgumentList '-m','app.modules.communications.case_email','--watch' -WorkingDirectory $sapsRoot -WindowStyle Hidden -PassThru
        Start-Sleep -Milliseconds 700
        $worker.Refresh()
        if ($worker.HasExited) { throw 'The app started, but its email worker failed. Check database and email configuration.' }
    }
    Write-Host 'SAPS and its case-email worker are running. Opening the home page...'
    Start-Process "$sapsUrl/portal/home.html"
    exit 0
} catch {
    # Fixed setup/launch errors only; never print environment variables or credentials.
    Write-Host 'Unable to complete startup.' -ForegroundColor Red
    if ($_.Exception -is [System.Management.Automation.RuntimeException]) {
        Write-Host $_.Exception.Message
    } else { Write-Host 'Check that Python, PostgreSQL and the local project configuration are available.' }
    exit 1
}
