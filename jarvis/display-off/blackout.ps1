# Screen "off" for Modern Standby laptops (the 5060): a real display-off (SC_MONITORPOWER) puts these machines into
# Modern Standby, which suspends the Worker, Ollama and TARS and drops the tailnet (10/01 05:17 -> 15:41). Instead this
# covers every screen with a black window and turns the backlight to 0, then puts both back on the first mouse or
# keyboard input. The machine itself never changes power state. Started detached by displays-off.ps1.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Namespace Jarvis -Name Idle -MemberDefinition @"
[StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
[DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
public static uint LastInput() {
    LASTINPUTINFO i = new LASTINPUTINFO(); i.cbSize = (uint)Marshal.SizeOf(i);
    GetLastInputInfo(ref i); return i.dwTime;
}
"@
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\JarvisBlackout', [ref]$created)
if (-not $created) { return }   # already blacked out

$log = Join-Path $PSScriptRoot 'displays-off.log'
$level = $null
try { $level = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness } catch {}
function Set-Brightness([int]$v) {
    try { Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop |
          ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName WmiSetBrightness -Arguments @{ Timeout = 0; Brightness = [byte]$v } | Out-Null } } catch {}
}

$forms = foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $f = New-Object System.Windows.Forms.Form
    $f.FormBorderStyle = 'None'; $f.BackColor = [System.Drawing.Color]::Black; $f.TopMost = $true
    $f.ShowInTaskbar = $false; $f.StartPosition = 'Manual'; $f.Bounds = $s.Bounds; $f.Cursor = [System.Windows.Forms.Cursors]::No
    $f.Show(); $f
}
[System.Windows.Forms.Cursor]::Hide()
Set-Brightness 0
Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  blackout on (Modern Standby safe)  brightness was {1}" -f (Get-Date), $level)

$start = [Jarvis.Idle]::LastInput()
Start-Sleep -Milliseconds 1500
$start = [Jarvis.Idle]::LastInput()   # ignore the input that may have come from launching it
while ([Jarvis.Idle]::LastInput() -eq $start) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 300
}
if ($null -ne $level) { Set-Brightness $level }
[System.Windows.Forms.Cursor]::Show()
$forms | ForEach-Object { $_.Close() }
Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  blackout off (input)" -f (Get-Date))
$mutex.ReleaseMutex()
