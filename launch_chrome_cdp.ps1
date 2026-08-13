# launch_chrome_cdp.ps1
# PowerShell equivalent of launch_chrome_cdp.sh for Windows
#
# Usage:
#   .\launch_chrome_cdp.ps1 "https://example.com" 9222
#   .\launch_chrome_cdp.ps1  # Uses defaults
#
# Requires: PowerShell 5.0+, Google Chrome
# Supports environment variables: TARGET_URL, CDP_PORT, CHROME_PROFILE

param(
    [string]$TargetUrl = "",
    [int]$CdpPort = 0
)

# Set defaults with env var fallback (compatible with PowerShell 5.0)
if ([string]::IsNullOrEmpty($TargetUrl)) {
    $TargetUrl = if ($env:TARGET_URL) { $env:TARGET_URL } else { "https://code.claude.com/docs/en/overview" }
}
if ($CdpPort -eq 0) {
    $CdpPort = if ($env:CDP_PORT) { [int]$env:CDP_PORT } else { 9222 }
}

# Chrome profile directory
$ChromeProfile = if ($env:CHROME_PROFILE) { $env:CHROME_PROFILE } else { "$env:USERPROFILE\AppData\Local\chrome-inspect" }

# Find Chrome installation
function Find-Chrome {
    $possiblePaths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
        "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Chromium\Application\chrome.exe"
    )

    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            return $path
        }
    }
    return $null
}

$ChromeBin = Find-Chrome
if (-not $ChromeBin) {
    Write-Host "[ERROR] Google Chrome not found" -ForegroundColor Red
    Write-Host "   Download from: https://www.google.com/chrome/" -ForegroundColor Yellow
    exit 1
}

Write-Host "[*] Chrome binary: $ChromeBin" -ForegroundColor Green
Write-Host "[*] CDP port: $CdpPort" -ForegroundColor Green
Write-Host "[*] Profile dir: $ChromeProfile" -ForegroundColor Green
Write-Host "[*] Target URL: $TargetUrl" -ForegroundColor Green
Write-Host ""

# Check if Chrome already running on CDP port
$portCheck = Get-NetTCPConnection -LocalPort $CdpPort -State Listen -ErrorAction SilentlyContinue
if ($portCheck) {
    Write-Host "[OK] Chrome already running on port $CdpPort" -ForegroundColor Green
    Write-Host "  Opening $TargetUrl in new window..." -ForegroundColor Yellow
    Start-Process -FilePath $ChromeBin -ArgumentList @("--new-window", $TargetUrl)
    Start-Sleep -Seconds 1
    Write-Host ""
    Write-Host "✅ Ready for testing. CDP endpoint: http://localhost:$CdpPort" -ForegroundColor Green
    exit 0
}

# Clean up old Chrome process if stuck on this port
$existingProc = Get-Process -Name chrome -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like "*remote-debugging-port=$CdpPort*" }
if ($existingProc) {
    Write-Host "[*] Cleaning up old Chrome process on port $CdpPort..." -ForegroundColor Yellow
    $existingProc | Stop-Process -Force -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
}

# Create profile directory if needed
if (-not (Test-Path $ChromeProfile)) {
    New-Item -ItemType Directory -Path $ChromeProfile -Force | Out-Null
}

Write-Host "[*] Launching Chrome with CDP..." -ForegroundColor Cyan
Write-Host ""

# Launch Chrome with CDP
$chromeArgs = @(
    "--remote-debugging-port=$CdpPort",
    "--user-data-dir=$ChromeProfile",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    $TargetUrl
)

$process = Start-Process -FilePath $ChromeBin -ArgumentList $chromeArgs -PassThru -WindowStyle Normal
if (-not $process) {
    Write-Host "[ERROR] Failed to launch Chrome" -ForegroundColor Red
    exit 1
}
$pid = $process.Id

Write-Host "[OK] Chrome launched (PID: $pid)" -ForegroundColor Green
Write-Host ""
Write-Host "[*] Waiting for CDP to be ready..." -ForegroundColor Yellow

# Wait for CDP to respond (max 30 seconds)
$maxWait = 30
$waited = 0
$ready = $false

while ($waited -lt $maxWait) {
    try {
        $connection = [System.Net.Sockets.TcpClient]::new("localhost", $CdpPort)
        if ($connection) {
            $connection.Close()
            $ready = $true
            break
        }
    }
    catch {
        # Port not ready yet
    }

    Start-Sleep -Milliseconds 500
    Write-Host -NoNewline "." -ForegroundColor Cyan
    $waited += 0.5
}

Write-Host ""
Write-Host ""

if ($ready) {
    Write-Host "[SUCCESS] CDP ready on http://localhost:$CdpPort" -ForegroundColor Green
    Write-Host ""
    Write-Host "Browser is open and ready for testing." -ForegroundColor Green
    Write-Host "You can now run:" -ForegroundColor Yellow
    Write-Host "  python run_interactive.py" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To stop Chrome: Stop-Process -Id $pid" -ForegroundColor Gray
}
else {
    Write-Host "[ERROR] CDP did not respond within $maxWait seconds" -ForegroundColor Red
    Write-Host "Chrome may still be starting. Try waiting and reconnecting." -ForegroundColor Yellow
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue | Out-Null
    exit 1
}
