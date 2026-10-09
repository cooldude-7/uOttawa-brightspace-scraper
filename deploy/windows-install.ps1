<#
Make this Windows laptop do what the Pi did: scrape hourly 07:00-21:00 and
serve the app, with nobody touching it.

    powershell -ExecutionPolicy Bypass -File deploy\windows-install.ps1

Safe to run again -- it replaces what it installed before. To remove:

    powershell -ExecutionPolicy Bypass -File deploy\windows-install.ps1 -Remove

WHY THE TASKS RUN ONLY WHILE YOU ARE SIGNED IN

A Brightspace login eventually expires, and logging back in needs a browser
window you can see. A task that runs "whether or not the user is signed in"
runs in an invisible session, so the window would open where nobody can
reach it. Running in your own session means the window appears on the
screen; collect.py waits five minutes for you and then gives up cleanly, so
an unattended expiry costs one failed hour, not a hung scraper.

After a Windows Update restart the machine sits at the sign-in screen and
nothing runs. Turn on Settings > Accounts > Sign-in options > "Use my sign-in
info to automatically finish setting up after an update" and Windows signs
you back in and locks the screen, so the tasks come back by themselves.
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$repo    = Split-Path -Parent $PSScriptRoot
$deploy  = Join-Path $repo "deploy"
$scraper = Join-Path $repo "scraper"
$names   = "Brightspace web", "Brightspace update"

foreach ($n in $names) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $n -Confirm:$false
    }
}
if ($Remove) {
    Write-Host "`n  Removed both tasks. Nothing runs by itself now.`n"
    return
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python is not on PATH -- install Python first."
}

# The exact Python this window uses, by full path. A machine can hold two
# (the ThinkPad had one from September and one from winget), and a scheduled
# task does not necessarily get the same PATH as the window you installed
# into: it started the other one, which had none of the packages, and the
# app died on its first import while this script printed "running now".
$py = (& python -c "import sys; print(sys.executable)" | Select-Object -First 1).Trim()
# No 2>$null: under "Stop", Windows PowerShell turns redirected stderr from a
# program into a terminating error, which would hide the message below.
# The traceback it prints names the missing package, which is useful anyway.
& $py -c "import fastapi, uvicorn, httpx, anthropic, fitz"
if ($LASTEXITCODE -ne 0) {
    throw ("$py is missing packages. From the repo folder run:`n" +
           "  `"$py`" -m pip install -r scraper\requirements-pi.txt playwright")
}
if (-not (Test-Path (Join-Path $scraper "brightspace.db"))) {
    Write-Warning "No scraper\brightspace.db yet. Run 'python backup.py --restore' and one 'python update.py' by hand first."
}

$me = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

# conhost --headless runs the .cmd with no console window flashing up every
# hour. A login browser, if one is needed, still opens normally.
# The Python path goes to the .cmd as its argument. cmd /c strips the outer
# pair of quotes, so both quoted paths sit inside one more pair.
function Launch($cmd) {
    New-ScheduledTaskAction -Execute "conhost.exe" `
        -Argument "--headless cmd.exe /c `"`"$(Join-Path $deploy $cmd)`" `"$py`"`"" `
        -WorkingDirectory $scraper
}

# --- the web app: at sign-in, restarted if it ever dies -------------------
$webSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Brightspace web" -Principal $principal `
    -Action (Launch "run-web.cmd") -Settings $webSettings `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $me) `
    -Description "Deadlines app on port 8000. See deploy\windows-install.ps1." | Out-Null

# --- the scrape: hourly 07:00-21:00 ---------------------------------------
# Daily at 07:00, repeating every hour for 14 hours. PowerShell has no direct
# way to say that, so the repetition is borrowed from a one-off trigger.
$daily = New-ScheduledTaskTrigger -Daily -At 7am
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At 7am `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition
# StartWhenAvailable: a run missed while the lid was shut on battery happens
# as soon as the machine is back. 50 minutes is a ceiling, not an estimate --
# a scrape takes a few minutes; this only stops a stuck one blocking the next.
$updSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 50) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Brightspace update" -Principal $principal `
    -Action (Launch "run-update.cmd") -Settings $updSettings -Trigger $daily `
    -Description "One scrape per hour, 07:00-21:00. See deploy\windows-install.ps1." | Out-Null

# --- lid shut and plugged in: keep running ---------------------------------
$failed = 0
foreach ($args_ in @(
        @("/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", "0"),
        # The ThinkPad slept on its first night with the lid set to "Do
        # nothing" on AC: Windows logged "Sleep Reason: Button or Lid", and
        # hid its lid settings, which is what it does when it does not fully
        # own the lid. Firmware then reports the lid as a sleep-button press,
        # and that button still said Sleep. On battery the lid also still
        # said Sleep, so a loose charger had the same effect. A machine whose
        # only job is to stay up should stay up through either.
        @("/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "SBUTTONACTION", "0"),
        @("/setdcvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", "0"),
        @("/change", "standby-timeout-ac", "0"),
        @("/change", "hibernate-timeout-ac", "0"),
        @("/setactive", "SCHEME_CURRENT"))) {
    powercfg @args_ | Out-Null
    if ($LASTEXITCODE -ne 0) { $failed++ }
}
$power = if ($failed) {
    "could NOT change all power settings -- set 'closing the lid' to 'Do nothing' (plugged in) by hand"
} else { "lid closed or open, it stays awake while plugged in" }

Start-ScheduledTask -TaskName "Brightspace web"

# Say "running" only once something is answering. Starting the task proves
# nothing -- the September Python started fine and died on its first line.
$up = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    $tcp = New-Object System.Net.Sockets.TcpClient
    try { $tcp.Connect("127.0.0.1", 8000); $up = $true } catch { } finally { $tcp.Close() }
    if ($up) { break }
}
$weblog = Join-Path $scraper "logs\web.log"

$ip = (Get-NetIPConfiguration -ErrorAction SilentlyContinue |
       Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
       Select-Object -First 1).IPv4Address.IPAddress

Write-Host ""
Write-Host "  Installed."
if ($up) {
    Write-Host "    web app   running now, and at every sign-in"
} else {
    Write-Host "    web app   NOT answering after 20 seconds. Last lines of web.log:" -ForegroundColor Red
    if (Test-Path $weblog) { Get-Content $weblog -Tail 8 | ForEach-Object { Write-Host "      $_" } }
}
Write-Host "    python    $py"
Write-Host "    scraping  every hour, 07:00 to 21:00"
Write-Host "    power     $power"
Write-Host ""
Write-Host "  Open the app:   http://localhost:8000"
if ($ip) { Write-Host "  From your phone on the same Wi-Fi:   http://${ip}:8000" }
Write-Host "  Logs:           scraper\logs\update.log and web.log"
Write-Host ""
