# Jarvis guardrails installer: run once on homebase (and the rig if Claude works there too).
# In a normal PowerShell window, paste:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/guardrails/install.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   - puts GUARDRAILS.md, policy.json and training_intake.py in C:\Jarvis\guardrails
#   - creates C:\Jarvis\training (and its _log folder) if missing
#   - adds allow rules to %USERPROFILE%\.claude\settings.json so Claude stops asking before it writes training
#     data, runs the intake script, rebuilds jarvis-fc, or edits the Jarvis work folders (backs the file up first)
# Safe to re-run: files are replaced and rules are only added once.

$ErrorActionPreference = 'Stop'
$ref  = if ($env:JARVIS_REF) { $env:JARVIS_REF } else { 'claude/eager-knuth-lakcxt' }
$dir  = 'C:\Jarvis\guardrails'
function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }

New-Item -ItemType Directory -Force -Path $dir, 'C:\Jarvis\training\_log' | Out-Null
# Flow is private, so files come through the contents API with Jake's GITHUB_TASKS_TOKEN.
$tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
$h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
if ($tok) { $h.Authorization = "Bearer $tok" }
foreach ($f in 'GUARDRAILS.md', 'policy.json', 'training_intake.py') {
    Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/guardrails/$f`?ref=$ref" -OutFile (Join-Path $dir $f)
}
Say "Guardrail files are in $dir"

# --- Claude Code allow rules (paths use Claude Code's //c/... form for C:\) -------------
$rules = @(
    'Read(//c/Jarvis/**)',
    'Edit(//c/Jarvis/training/**)',
    'Edit(//c/Jarvis/jobs/**)',
    'Edit(//c/Jarvis/outputs/**)',
    'Edit(//c/Jarvis/reports/**)',
    'Edit(//c/Jarvis/work/**)',
    'Bash(python C:\Jarvis\guardrails\training_intake.py *)',
    'Bash(python C:/Jarvis/guardrails/training_intake.py *)',
    'Bash(py C:\Jarvis\guardrails\training_intake.py *)',
    'Bash(ollama create jarvis-fc*)',
    'Bash(ollama cp jarvis-fc*)',
    'Bash(ollama list*)',
    'Bash(ollama show jarvis-fc*)'
)
$claudeDir = Join-Path $env:USERPROFILE '.claude'
$sf = Join-Path $claudeDir 'settings.json'
New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
if (Test-Path $sf) {
    Copy-Item $sf "$sf.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    $s = Get-Content $sf -Raw | ConvertFrom-Json
} else {
    $s = [pscustomobject]@{}
}
if (-not $s) { $s = [pscustomobject]@{} }
if (-not $s.PSObject.Properties['permissions']) {
    $s | Add-Member -NotePropertyName permissions -NotePropertyValue ([pscustomobject]@{})
}
$allow = @(@($s.permissions.allow) | Where-Object { $_ })
$added = @($rules | Where-Object { $_ -notin $allow })
$merged = [object[]]($allow + $added)
if ($s.permissions.PSObject.Properties['allow']) { $s.permissions.allow = $merged }
else { $s.permissions | Add-Member -NotePropertyName allow -NotePropertyValue $merged }
[IO.File]::WriteAllText($sf, ($s | ConvertTo-Json -Depth 32), (New-Object Text.UTF8Encoding $false))
Say "Claude Code allow rules: $($added.Count) added, $($rules.Count - $added.Count) already there ($sf)"

# --- Check -----------------------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    & python (Join-Path $dir 'training_intake.py') list | Out-Host
} else {
    Say 'python is not on PATH, so the intake script could not be checked.' 'Yellow'
}
Say ''
Say '== Done ==' 'Green'
Say 'Claude can now add training data without asking. Each batch is logged in C:\Jarvis\training\_log\intake.jsonl' 'Green'
Say 'Undo a batch:  python C:\Jarvis\guardrails\training_intake.py revert <batch>' 'Green'
Say 'Restart the Worker and Remote Control (or reboot) so running Claude sessions pick up the new rules.' 'Yellow'
