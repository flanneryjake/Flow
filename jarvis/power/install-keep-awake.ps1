# Keeps a Jarvis PC awake and on the tailnet while plugged in, so Remote Control, the Worker, Ollama and TARS stay
# reachable. Written for the 5060 laptop (LAPTOP-4150EGRS), which kept dropping off: it is a Modern Standby
# (S0 Low Power Idle) machine, and on those "sleep" means the screen goes off and every desktop program (Remote
# Control, the Worker, Tailscale's tray, Ollama) is paused until someone touches it. Safe on the desktops too.
#
# What it does:
#   1. On plugged-in (AC) power, in EVERY power plan (so a vendor tool switching plans can't undo it):
#      sleep never, hibernate never, unattended sleep never, closing the lid does nothing, and the screen turns
#      itself off after $env:JARVIS_DISPLAY_IDLE_MIN minutes idle (default 5). Windows' own idle screen-off keeps
#      everything running, even on Modern Standby; only sleep, the power button and the lid start standby there.
#      Battery settings are left alone.
#   2. Registers "Jarvis Keep Awake" (at logon, hidden): holds a "system required" request for as long as it runs,
#      which blocks idle sleep whatever the plan says, and every 10 minutes puts the AC settings above back if
#      something changed them (logged to C:\Jarvis\power\keep-awake.log).
#   3. Reports whether this PC is Modern Standby. On one, don't turn screens off by posting SC_MONITORPOWER (what
#      jarvis/display-off does): Windows treats that like the power button and the PC goes to standby.
#
# Run in a normal PowerShell window (no admin needed):
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/<commit>/jarvis/power/install-keep-awake.ps1 | iex
# Remove: set $env:JARVIS_KEEP_AWAKE_REMOVE = '1' before running (the power settings stay as they are).

$ErrorActionPreference = 'Stop'
$taskName = 'Jarvis Keep Awake'
$dir = 'C:\Jarvis\power'

if ($env:JARVIS_KEEP_AWAKE_REMOVE -eq '1') {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed $taskName" -ForegroundColor Green
    return
}

$displayMin = if ($env:JARVIS_DISPLAY_IDLE_MIN) { [int]$env:JARVIS_DISPLAY_IDLE_MIN } else { 5 }
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# The guard script: also used once below to apply the settings to every plan.
$ps1 = Join-Path $dir 'keep-awake.ps1'
Set-Content -Path $ps1 -Encoding ASCII -Value @"
# Holds a 'system required' request and keeps the AC power settings in place. Written by install-keep-awake.ps1.
# -ApplyAll: set every power plan once and exit (the installer runs this).
param([switch]`$ApplyAll)
`$log = 'C:\Jarvis\power\keep-awake.log'
`$want = [ordered]@{
    'SUB_SLEEP STANDBYIDLE'   = 0     # sleep after: never
    'SUB_SLEEP HIBERNATEIDLE' = 0     # hibernate after: never
    'SUB_SLEEP UNATTENDSLEEP' = 0     # unattended sleep: never
    'SUB_BUTTONS LIDACTION'   = 0     # closing the lid: do nothing
    'SUB_VIDEO VIDEOIDLE'     = $($displayMin * 60)   # screen off after idle (seconds); the PC keeps running
}
function Log([string]`$m) { Add-Content -Path `$log -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), `$m) }
function AcValue([string]`$scheme, [string]`$sub, [string]`$setting) {
    `$out = powercfg /query `$scheme `$sub `$setting 2>`$null | Out-String
    if (`$out -match 'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') { return [Convert]::ToInt64(`$Matches[1], 16) }
    return `$null   # setting hidden or missing on this PC
}
function Enforce([string]`$scheme) {
    `$changed = @()
    foreach (`$k in `$want.Keys) {
        `$sub, `$setting = `$k -split ' '
        `$cur = AcValue `$scheme `$sub `$setting
        if (`$null -ne `$cur -and `$cur -ne `$want[`$k]) {
            powercfg /setacvalueindex `$scheme `$sub `$setting `$want[`$k] 2>&1 | Out-Null
            `$changed += "`$setting `$cur->`$(`$want[`$k])"
        }
    }
    return `$changed
}
function Schemes { (powercfg /list) -match 'GUID:' | ForEach-Object { if (`$_ -match '([0-9a-fA-F-]{36})\s+\((.+?)\)') { [pscustomobject]@{ Guid = `$Matches[1]; Name = `$Matches[2]; Active = `$_ -match '\*\s*$' } } } }

if (`$ApplyAll) {
    foreach (`$s in Schemes) {
        `$c = Enforce `$s.Guid
        `$msg = if (`$c) { 'set ' + (`$c -join ', ') } else { 'already right' }
        Write-Host ("{0,-28} {1}" -f `$s.Name, `$msg)
        if (`$c) { Log "plan '`$(`$s.Name)': `$msg" }
    }
    powercfg /setactive SCHEME_CURRENT 2>&1 | Out-Null
    return
}

Add-Type -Namespace Jarvis -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no idle sleep while this process lives. The screen can still turn off.
[void][Jarvis.Power]::SetThreadExecutionState([uint32]2147483649)
Log "started (pid `$PID), holding system-required"
while (`$true) {
    try {
        `$active = Schemes | Where-Object Active | Select-Object -First 1
        if (`$active) {
            `$c = Enforce `$active.Guid
            if (`$c) { powercfg /setactive `$active.Guid 2>&1 | Out-Null; Log "plan '`$(`$active.Name)' had drifted, put back: `$(`$c -join ', ')" }
        }
    } catch { Log "check failed: `$(`$_.Exception.Message)" }
    Start-Sleep -Seconds 600
}
"@

Write-Host '== Power settings on AC, every plan ==' -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -ApplyAll

$vbs = Join-Path $dir 'keep-awake.vbs'
Set-Content -Path $vbs -Encoding ASCII -Value @(
    "' Written by install-keep-awake.ps1: runs keep-awake.ps1 with no window and waits.",
    'Set sh = CreateObject("WScript.Shell")',
    "WScript.Quit sh.Run(""powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """"$ps1"""""", 0, True)"
)
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument "//B //Nologo `"$vbs`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Keeps this PC from sleeping on AC so Jarvis stays reachable (screen can still turn off). Flow jarvis/power.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "installed and started $taskName (log: $dir\keep-awake.log)" -ForegroundColor Green

$states = (powercfg /a | Out-String) -split '(?im)^.*not available.*$' | Select-Object -First 1
if ($states -match 'S0 Low Power Idle') {
    Write-Host 'This PC is Modern Standby: posting SC_MONITORPOWER (jarvis/display-off) puts it to sleep. Let the idle screen-off above do it instead.' -ForegroundColor Yellow
} else {
    Write-Host 'This PC uses classic sleep (S3); turning the screens off by message is safe here.'
}
