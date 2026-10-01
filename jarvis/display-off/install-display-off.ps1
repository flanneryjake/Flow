# Nightly "displays off" for every Jarvis PC (homebase, rig, laptop).
# Registers the scheduled task "Jarvis Displays Off": every day at 23:00 it turns the monitors off to save power.
# Only the screens go dark. Nothing sleeps, hibernates or shuts down, and Remote Control, the Workers, Ollama and
# Tailscale keep running. Moving the mouse or pressing a key turns the screens back on as usual.
#
# Run in a normal PowerShell window on each PC (no admin needed; the task runs as you, in your desktop session):
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/<commit>/jarvis/display-off/install-display-off.ps1 | iex
# Test right away (screens go dark within a second or two):  Start-ScheduledTask -TaskName 'Jarvis Displays Off'
# Different time:  set $env:JARVIS_DISPLAY_OFF_AT = '22:30' before running.   Remove:  set $env:JARVIS_DISPLAY_OFF_REMOVE = '1'.
#
# How it works: C:\Jarvis\display-off\displays-off.ps1 posts WM_SYSCOMMAND / SC_MONITORPOWER (2 = off) to all
# top-level windows. PostMessage (not SendMessage) so a hung window can't block it. The task starts it through
# wscript.exe and a .vbs with window style 0, the same pattern as hide-task-windows.ps1, so no console flashes.

$ErrorActionPreference = 'Stop'
$taskName = 'Jarvis Displays Off'
$dir = 'C:\Jarvis\display-off'

if ($env:JARVIS_DISPLAY_OFF_REMOVE -eq '1') {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed $taskName" -ForegroundColor Green
    return
}

$at = if ($env:JARVIS_DISPLAY_OFF_AT) { $env:JARVIS_DISPLAY_OFF_AT } else { '23:00' }
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$ps1 = Join-Path $dir 'displays-off.ps1'
Set-Content -Path $ps1 -Encoding ASCII -Value @'
# Turns all monitors off (they wake on mouse/keyboard). Written by install-display-off.ps1.
Add-Type -Namespace Jarvis -Name Monitor -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, System.IntPtr lParam);
"@
# HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = power off
$ok = [Jarvis.Monitor]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)
Add-Content -Path (Join-Path $PSScriptRoot 'displays-off.log') -Value ("{0:yyyy-MM-dd HH:mm:ss}  displays off  posted={1}" -f (Get-Date), $ok)
'@

$vbs = Join-Path $dir 'displays-off.vbs'
Set-Content -Path $vbs -Encoding ASCII -Value @(
    "' Written by install-display-off.ps1: runs displays-off.ps1 with no window and waits.",
    'Set sh = CreateObject("WScript.Shell")',
    "WScript.Quit sh.Run(""powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """"$ps1"""""", 0, True)"
)

# Interactive logon: the message has to reach the logged-in desktop, which a session-0 (S4U) task can't touch.
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument "//B //Nologo `"$vbs`""
$trigger = New-ScheduledTaskTrigger -Daily -At $at
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Turns the monitors off every night (nothing sleeps or stops). Flow jarvis/display-off.' -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
Write-Host "installed $taskName for $user, daily at $at (next run $((Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime))" -ForegroundColor Green
Write-Host "test now: Start-ScheduledTask -TaskName '$taskName'   (log: $dir\displays-off.log)"
