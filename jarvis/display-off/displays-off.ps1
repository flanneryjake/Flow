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
# Modern Standby (S0 low power idle) machines treat SC_MONITORPOWER as "go to sleep": the 5060 laptop went into
# standby at 10/01 05:17 and dropped off the tailnet until someone pressed a key. On those, black the screens out
# instead (blackout.ps1, detached) and leave the power state alone.
$available = ((powercfg /a) -join "`n") -split 'not available' | Select-Object -First 1
if ($available -match 'S0 Low Power Idle') {
    $bo = Join-Path $PSScriptRoot 'blackout.ps1'
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File `"$bo`""
    return
}
# HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = power off
$ok = [Jarvis.Monitor]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)
Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  displays off  posted={1}  idle={2}min" -f (Get-Date), $ok, $idleMin)
