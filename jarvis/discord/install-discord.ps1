# Jarvis in Discord: run ONCE on the homebase (the 5060 laptop), as Jake's normal user (no admin needed).
# In a normal PowerShell window, paste:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/discord/install-discord.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   - saves jarvis_discord.py to C:\Jarvis\discord and installs discord.py for this user (pip --user)
#   - finds the bot token: C:\Jarvis\secrets\discord-bot.txt, else a discord-bot.txt the rig sent over Tailscale
#     (`tailscale file get`, or Downloads where Windows Tailscale saves it), else asks for it once. It is never printed.
#   - registers the "Jarvis Discord" task: starts at logon, and every 5 minutes starts it again if it died
#   - starts it and checks http://127.0.0.1:8796/health
# Safe to re-run (it updates the script and restarts the bot).

$ErrorActionPreference = 'Stop'
$flowRef = 'claude/eager-knuth-lakcxt'
function Get-FlowFile([string]$path, [string]$out) {
    $tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
    $h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
    if ($tok) { $h.Authorization = "Bearer $tok" }
    try { Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/$path`?ref=$flowRef" -OutFile $out }
    catch { throw "Could not download jarvis/$path from Flow ($_). Check that GITHUB_TASKS_TOKEN can read flanneryjake/Flow." }
}
function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }
$dir = 'C:\Jarvis\discord'
$secrets = 'C:\Jarvis\secrets'
$tokenFile = Join-Path $secrets 'discord-bot.txt'

$py = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $py) { throw 'Python is not on PATH for this user.' }
$pyw = Join-Path (Split-Path $py.Source) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py.Source }

New-Item -ItemType Directory -Force -Path $dir, $secrets | Out-Null
if ($env:JARVIS_DISCORD_SRC) { Copy-Item (Join-Path $env:JARVIS_DISCORD_SRC 'jarvis_discord.py') $dir -Force }
else { Get-FlowFile 'discord/jarvis_discord.py' (Join-Path $dir 'jarvis_discord.py') }
Say "Saved jarvis_discord.py to $dir"

& $py.Source -m pip install --user --quiet --disable-pip-version-check 'discord.py>=2.4'
if ($LASTEXITCODE -ne 0) { throw 'pip could not install discord.py.' }

# --- token
if (-not (Test-Path $tokenFile)) {
    $ts = Get-Command tailscale -ErrorAction SilentlyContinue
    if (-not $ts) { $ts = Get-Item 'C:\Program Files\Tailscale\tailscale.exe' -ErrorAction SilentlyContinue }
    if ($ts) {
        $inbox = Join-Path $env:TEMP 'jarvis-ts-inbox'
        New-Item -ItemType Directory -Force -Path $inbox | Out-Null
        & $ts.Source file get --conflict=rename $inbox 2>$null | Out-Null
        $got = Get-ChildItem $inbox -Filter 'discord-bot*.txt' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
        if ($got) { Move-Item $got.FullName $tokenFile -Force; Say 'Took the bot token the rig sent over Tailscale.' }
        # anything else that was waiting (e.g. another key file) stays in the inbox folder for whoever expects it
        $rest = Get-ChildItem $inbox -ErrorAction SilentlyContinue
        if ($rest) { Say "Other files received over Tailscale are in $inbox : $($rest.Name -join ', ')" 'Yellow' }
    }
}
if (-not (Test-Path $tokenFile)) {
    # Tailscale on Windows saves Taildrop files straight into Downloads instead of the `file get` inbox
    $dl = Get-ChildItem (Join-Path $env:USERPROFILE 'Downloads') -Filter 'discord-bot*.txt' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if ($dl) { Move-Item $dl.FullName $tokenFile -Force; Say 'Took the bot token the rig sent (it was in Downloads).' }
}
if (-not (Test-Path $tokenFile)) {
    $sec = Read-Host 'Discord bot token' -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    [IO.File]::WriteAllText($tokenFile, $plain.Trim())
}
# only this user and SYSTEM can read the token
& icacls $tokenFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" 'SYSTEM:(F)' | Out-Null

# --- task
Get-ScheduledTask -TaskName 'Jarvis Discord' -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like '*jarvis_discord.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\jarvis_discord.py`"" -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
# IgnoreNew: the 5-minute trigger only starts the bot when it isn't already running
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Jarvis Discord' -Action $action -Trigger @($logon, $every) -Settings $settings `
    -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName 'Jarvis Discord'
Say 'Registered and started the "Jarvis Discord" task.'

$ok = $false
foreach ($i in 1..12) {
    Start-Sleep -Seconds 5
    try { $h = Invoke-RestMethod 'http://127.0.0.1:8796/health' -TimeoutSec 5; if ($h.ok) { $ok = $true; break } } catch {}
}
if ($ok) { Say "Jarvis is online in Discord as $($h.user) in $($h.servers) server(s), $($h.alert_channels) alert channel(s)." 'Green' }
else { Say "The bot did not come up; see $dir\discord.log" 'Red'; Get-Content "$dir\discord.log" -Tail 20 -ErrorAction SilentlyContinue }
