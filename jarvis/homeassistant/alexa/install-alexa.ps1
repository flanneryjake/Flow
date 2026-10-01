# Homebase installer for the Jarvis <-> Alexa bridge. Run from this folder (a Remote Control session does it):
#   powershell -ExecutionPolicy Bypass -File install-alexa.ps1
# 1. copies the bridge to C:\Jarvis\alexa  2. installs Alexa Media Player into HA (WSL)
# 3. logs the jarvis-bot user in  4. schedules the nightly routine export (4:05 AM, hidden)
$ErrorActionPreference = 'Stop'
$dest = 'C:\Jarvis\alexa'
New-Item -ItemType Directory -Force -Path "$dest\packages", 'C:\Jarvis\routine' | Out-Null
Copy-Item "$PSScriptRoot\jarvis_alexa.py", "$PSScriptRoot\install-alexa.sh", "$PSScriptRoot\README.md" $dest -Force
Copy-Item "$PSScriptRoot\packages\alexa.yaml" "$dest\packages" -Force

# WSL wants LF line endings on the shell script.
$sh = "$dest\install-alexa.sh"
[IO.File]::WriteAllText($sh, ((Get-Content $sh -Raw) -replace "`r`n", "`n"))
wsl -e bash /mnt/c/Jarvis/alexa/install-alexa.sh
if ($LASTEXITCODE -eq 2) { Write-Host 'Stopped: HA onboarding not finished yet.'; exit 2 }
if ($LASTEXITCODE -ne 0) { throw "install-alexa.sh failed ($LASTEXITCODE)" }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py).Source }
& $py "$dest\jarvis_alexa.py" login
if ($LASTEXITCODE -ne 0) { throw 'jarvis-bot login failed' }

$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dest\jarvis_alexa.py`" export" -WorkingDirectory $dest
$trigger = New-ScheduledTaskTrigger -Daily -At 4:05am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName 'Jarvis Routine Export' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Yesterday''s Home Assistant state changes (Alexa, lights, presence) -> C:\Jarvis\routine' -Force | Out-Null
Write-Host 'ok: scheduled Jarvis Routine Export (daily 4:05 AM)'
& $py "$dest\jarvis_alexa.py" devices
