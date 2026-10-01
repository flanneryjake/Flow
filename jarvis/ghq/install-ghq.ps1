# Jarvis GitHub task queue: run ONCE per machine (homebase first, then the rig).
# In a normal PowerShell window, paste:
#
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/ghq/install-ghq.ps1 | iex
#
# What it does:
#   - saves ghq.py (the queue client) and migrate_notion.py to C:\Jarvis\ghq
#   - asks once for the GitHub token and stores it as your user variable GITHUB_TASKS_TOKEN (never printed)
#   - creates the labels and the pinned "Health: homebase" / "Health: rig" issues (safe to re-run)
#   - homebase only: shows what the Notion copy would create, then copies the open cards if you type Y
# Notion is only read. The Workers keep using Notion until agent.py is switched over.

$ErrorActionPreference = 'Stop'
$base = 'https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis'
$dir  = 'C:\Jarvis\ghq'
$repo = 'flanneryjake/jarvis-tasks'
function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }

$machine = (Get-Content 'C:\Jarvis\watchdog\machine.txt' -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $machine) {
    $machine = switch ($env:COMPUTERNAME.ToUpper()) { 'DESKTOP-5VE3C77' { 'homebase' } 'DESKTOP-VLLDDM4' { 'rig' } 'LAPTOP-4150EGRS' { 'laptop' } default { '' } }
}
if (-not $machine) { $machine = (Read-Host "Is this 'homebase', 'rig' or 'laptop'?").Trim().ToLower() }
$machine = "$machine".Trim()

$py = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $py) { throw 'Python is not on PATH for this user.' }

New-Item -ItemType Directory -Force -Path $dir | Out-Null
foreach ($f in 'ghq.py', 'migrate_notion.py') {
    Invoke-WebRequest -UseBasicParsing "$base/ghq/$f" -OutFile (Join-Path $dir $f)
}
Say "Saved the queue scripts to $dir"

$token = [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')
if (-not $token) {
    Say "Paste the GitHub token for $repo (input is hidden):" 'Yellow'
    $sec = Read-Host -AsSecureString
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if (-not $token) { throw 'No token entered.' }
    [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN', $token, 'User')
}
[Environment]::SetEnvironmentVariable('JARVIS_TASKS_REPO', $repo, 'User')
$env:GITHUB_TASKS_TOKEN = $token
$env:JARVIS_TASKS_REPO  = $repo

Say 'Creating labels and health issues...'
& $py.Source (Join-Path $dir 'ghq.py') setup
if ($LASTEXITCODE -ne 0) { throw 'Setup failed. Check that the repo exists and the token has Issues: Read and write on it.' }

if ($machine -eq 'homebase') {
    $env:NOTION_TOKEN = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')
    if (-not $env:NOTION_TOKEN) { $env:NOTION_TOKEN = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'Machine') }
    if ($env:NOTION_TOKEN) {
        Push-Location $dir
        & $py.Source migrate_notion.py
        if ((Read-Host 'Copy these Notion cards to GitHub now? (Y/N)') -match '^[Yy]') { & $py.Source migrate_notion.py --apply }
        Pop-Location
    } else {
        Say 'NOTION_TOKEN not found, so the Notion copy was skipped.' 'Yellow'
    }
}
Say "Done. Board: https://github.com/$repo/issues" 'Green'
