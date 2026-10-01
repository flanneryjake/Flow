# Jarvis GitHub task queue: run ONCE per machine (homebase first, then the rig).
# In a normal PowerShell window, paste:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); if(!$t){$t=Read-Host 'GitHub token'; [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN',$t,'User')}; irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/ghq/install-ghq.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   - saves ghq.py (the queue client) and migrate_notion.py to C:\Jarvis\ghq
#   - asks once for the GitHub token and stores it as your user variable GITHUB_TASKS_TOKEN (never printed)
#   - creates the labels and the pinned "Health: homebase" / "Health: rig" issues (safe to re-run)
#   - homebase only: shows what the Notion copy would create, then copies the open cards if you type Y
# Notion is only read. The Workers keep using Notion until agent.py is switched over.

$ErrorActionPreference = 'Stop'
$flowRef = 'claude/eager-knuth-lakcxt'
# Flow is private, so files come through the GitHub API with this user's GITHUB_TASKS_TOKEN
# (the token needs Contents: Read-only on flanneryjake/Flow).
function Get-FlowFile([string]$path, [string]$out) {
    $tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
    $h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
    if ($tok) { $h.Authorization = "Bearer $tok" }
    try { Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/$path`?ref=$flowRef" -OutFile $out }
    catch { throw "Could not download jarvis/$path from Flow ($_). Check that GITHUB_TASKS_TOKEN can read flanneryjake/Flow." }
}
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
    Get-FlowFile "ghq/$f" (Join-Path $dir $f)
}
Say "Saved the queue scripts to $dir"

$token = [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')
if (-not $token) {
    Say "Paste the GitHub token for $repo and flanneryjake/Flow (input is hidden):" 'Yellow'
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
