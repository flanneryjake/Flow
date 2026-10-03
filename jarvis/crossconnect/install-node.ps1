<#
Install the Jarvis node agent on this PC (cross-connect phase 2+). Run from the folder holding this script, elevated.

  powershell -ExecutionPolicy Bypass -File install-node.ps1 -Node 5060
  powershell -ExecutionPolicy Bypass -File install-node.ps1 -Node rig

What it does, nothing else:
  1. pip installs the `cryptography` package for this user's Python.
  2. Copies the agent to C:\Jarvis\crossconnect (keeps an existing node.json and state\).
  3. Writes node.json from examples\node.<Node>.json with this PC's tailnet address. Phase 2 switches off restart_pc.
  4. Makes this PC's key pair once (never overwrites). The private key stays in C:\Jarvis\secrets; the PUBLIC key is
     printed and saved to pubkeys\<Node>.pub.
  5. Windows Firewall: TCP 8799 inbound from the tailnet range 100.64.0.0/10 only.
  6. Scheduled task "Jarvis Node Agent": at startup, highest privileges, restarts itself on failure. Then starts it
     and checks /health.
#>
param(
    [Parameter(Mandatory)][ValidateSet('5060', 'rig', 'junk', 'hal9000')][string]$Node,
    [string[]]$Disable = @('restart_pc')
)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = 'C:\Jarvis\crossconnect'
$secrets = 'C:\Jarvis\secrets'
$selfKey = Join-Path $secrets 'crossconnect-self.json'

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction Stop).Source }
Write-Host "Python: $py"

# 1. cryptography
& $py -m pip install --user --quiet --disable-pip-version-check 'cryptography>=42'
if ($LASTEXITCODE) { throw 'pip install cryptography failed' }

# 2. files
New-Item -ItemType Directory -Force -Path $dst, (Join-Path $dst 'pubkeys'), (Join-Path $dst 'state'), $secrets | Out-Null
foreach ($f in 'node_agent.py', 'client.py', 'approvals.json', 'README.md') {
    Copy-Item (Join-Path $src $f) (Join-Path $dst $f) -Force
}
Copy-Item (Join-Path $src 'pubkeys\*.pub') (Join-Path $dst 'pubkeys') -Force -ErrorAction SilentlyContinue

# 3. node.json
$bind = (& tailscale ip -4 | Select-Object -First 1).Trim()
if ($bind -notlike '100.*') { throw "tailscale ip -4 gave '$bind'; is Tailscale up?" }
$cfgPath = Join-Path $dst 'node.json'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
} else {
    $cfg = Get-Content (Join-Path $src "examples\node.$Node.json") -Raw | ConvertFrom-Json
}
$cfg.bind = $bind
$cfg | Add-Member -NotePropertyName disabled -NotePropertyValue $Disable -Force
[IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Host "node.json: $Node on ${bind}:8799, switched off: $($Disable -join ', ')"

# 4. key pair (private stays here)
if (-not (Test-Path $selfKey)) {
    $pub = (& $py (Join-Path $dst 'node_agent.py') --make-key $Node --self-key $selfKey).Trim()
    if ($LASTEXITCODE) { throw 'making the key pair failed' }
    Set-Content (Join-Path $dst "pubkeys\$Node.pub") $pub -Encoding ASCII
}
$pubFile = Join-Path $dst "pubkeys\$Node.pub"
if (Test-Path $pubFile) { Write-Host "PUBLIC KEY ($Node): $((Get-Content $pubFile -Raw).Trim())" }

# 5. firewall, tailnet only
if (-not (Get-NetFirewallRule -DisplayName 'Jarvis Node Agent 8799' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'Jarvis Node Agent 8799' -Direction Inbound -Protocol TCP -LocalPort 8799 `
        -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
}

# 6. startup task
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$dst\node_agent.py`" --config `"$cfgPath`"" -WorkingDirectory $dst
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName 'Jarvis Node Agent' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Stop-ScheduledTask -TaskName 'Jarvis Node Agent' -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName 'Jarvis Node Agent'
Start-Sleep -Seconds 4
try {
    $h = Invoke-RestMethod "http://${bind}:8799/health" -TimeoutSec 5
    Write-Host "health: ok=$($h.ok) node=$($h.node)"
} catch {
    Write-Host "health check failed: $($_.Exception.Message)"
    exit 1
}
