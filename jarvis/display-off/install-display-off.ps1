# Screens-off schedule for every Jarvis PC (homebase, rig, laptop). Only the screens go dark: nothing sleeps,
# hibernates or shuts down, and Remote Control, the Workers, Ollama and Tailscale keep running. Moving the mouse or
# pressing a key turns the screens back on as usual. Registers two scheduled tasks:
#   "Jarvis Displays Off"          every day at 23:00, turns the monitors off.
#   "Jarvis Displays Off Daytime"  Tuesday to Saturday, 05:15 to 13:45: every 5 minutes it turns the monitors off
#                                  again if nobody has touched the mouse or keyboard for 3 minutes, so they stay off
#                                  for the whole window without blanking a screen someone is using.
#
# Run in a normal PowerShell window on each PC (no admin needed; the tasks run as you, in your desktop session):
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/<commit>/jarvis/display-off/install-display-off.ps1 | iex
# Test right away (screens go dark within a second or two):  Start-ScheduledTask -TaskName 'Jarvis Displays Off'
# Different night time:  set $env:JARVIS_DISPLAY_OFF_AT = '22:30' before running.
# Remove both tasks:     set $env:JARVIS_DISPLAY_OFF_REMOVE = '1' before running.
#
# How it works: C:\Jarvis\display-off\displays-off.ps1 posts WM_SYSCOMMAND / SC_MONITORPOWER (2 = off) to all
# top-level windows. PostMessage (not SendMessage) so a hung window can't block it. With -IfIdleMinutes N it first
# checks GetLastInputInfo and does nothing if there was input in the last N minutes. The tasks start it through
# wscript.exe and a .vbs with window style 0, the same pattern as hide-task-windows.ps1, so no console flashes.

$ErrorActionPreference = 'Stop'
$taskName = 'Jarvis Displays Off'
$dayTaskName = 'Jarvis Displays Off Daytime'
$dir = 'C:\Jarvis\display-off'

if ($env:JARVIS_DISPLAY_OFF_REMOVE -eq '1') {
    foreach ($n in $taskName, $dayTaskName) {
        Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "removed $n" -ForegroundColor Green
    }
    return
}

$at = if ($env:JARVIS_DISPLAY_OFF_AT) { $env:JARVIS_DISPLAY_OFF_AT } else { '23:00' }
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$ps1 = Join-Path $dir 'displays-off.ps1'
Set-Content -Path $ps1 -Encoding ASCII -Value @'
# Turns all monitors off (they wake on mouse/keyboard). Written by install-display-off.ps1.
# -IfIdleMinutes N: only if there was no mouse/keyboard input for N minutes.
param([int]$IfIdleMinutes = 0)
Add-Type -Namespace Jarvis -Name Monitor -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, System.IntPtr lParam);
[StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
[DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
public static uint IdleMs() {
    LASTINPUTINFO i = new LASTINPUTINFO(); i.cbSize = (uint)Marshal.SizeOf(i);
    if (!GetLastInputInfo(ref i)) return 0;
    return unchecked((uint)System.Environment.TickCount - i.dwTime);
}
"@
$log = Join-Path $PSScriptRoot 'displays-off.log'
$idleMin = [Math]::Floor([Jarvis.Monitor]::IdleMs() / 60000)
if ($IfIdleMinutes -gt 0 -and $idleMin -lt $IfIdleMinutes) { return }   # someone is using it; try again next run
# HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = power off
$ok = [Jarvis.Monitor]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)
Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  displays off  posted={1}  idle={2}min" -f (Get-Date), $ok, $idleMin)
'@

# One launcher per task; the .vbs passes its own arguments on to displays-off.ps1.
function Write-Launcher([string]$path, [string]$psArgs) {
    Set-Content -Path $path -Encoding ASCII -Value @(
        "' Written by install-display-off.ps1: runs displays-off.ps1 with no window and waits.",
        'Set sh = CreateObject("WScript.Shell")',
        "WScript.Quit sh.Run(""powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """"$ps1"""" $psArgs"", 0, True)"
    )
}
$vbs = Join-Path $dir 'displays-off.vbs'
$dayVbs = Join-Path $dir 'displays-off-daytime.vbs'
Write-Launcher $vbs ''
Write-Launcher $dayVbs '-IfIdleMinutes 3'

# Interactive logon: the message has to reach the logged-in desktop, which a session-0 (S4U) task can't touch.
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument "//B //Nologo `"$vbs`""
$trigger = New-ScheduledTaskTrigger -Daily -At $at
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Turns the monitors off every night (nothing sleeps or stops). Flow jarvis/display-off.' -Force | Out-Null

# Daytime: Tue-Sat from 05:15, repeating every 5 minutes for 8.5 hours (last run 13:45).
$dayAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument "//B //Nologo `"$dayVbs`""
$dayTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday, Wednesday, Thursday, Friday, Saturday -At '05:15'
$dayTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '05:15' -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 8 -Minutes 31)).Repetition
Register-ScheduledTask -TaskName $dayTaskName -Action $dayAction -Trigger $dayTrigger -Principal $principal -Settings $settings `
    -Description 'Keeps the monitors off Tue-Sat 05:15-13:45 while idle (nothing sleeps or stops). Flow jarvis/display-off.' -Force | Out-Null

foreach ($n in $taskName, $dayTaskName) {
    Write-Host "installed $n for $user (next run $((Get-ScheduledTaskInfo -TaskName $n).NextRunTime))" -ForegroundColor Green
}
Write-Host "test now: Start-ScheduledTask -TaskName '$taskName'   (log: $dir\displays-off.log)"
