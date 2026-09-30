# Stops Jarvis scheduled tasks from flashing a console window.
# A task that starts powershell.exe / cmd.exe / python.exe as the logged-in user opens a console for a split second,
# even with -WindowStyle Hidden. This rewrites each such "Jarvis*" task to start through wscript.exe and a small .vbs
# that runs the exact same command with window style 0 (never shown) and waits for it, so the task's run time,
# "don't start a second instance" and time limits behave as before.
#
# Run in a normal PowerShell window on each PC (no admin needed for tasks you registered yourself):
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/<commit>/jarvis/hide-task-windows.ps1 | iex
# Undo: run it again with $env:JARVIS_UNHIDE = '1' set first; the original actions are saved in C:\Jarvis\hidden\.
#
# Left alone on purpose: "Jarvis Remote Control" (long-running, starts once at logon, and it needs its console),
# tasks already going through wscript.exe/pythonw.exe, and tasks this user can't modify (reported, not changed).

$ErrorActionPreference = 'Continue'
$dir  = 'C:\Jarvis\hidden'
$save = Join-Path $dir 'original-actions.json'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$consoleExes = 'powershell.exe', 'pwsh.exe', 'cmd.exe', 'python.exe', 'py.exe', 'conhost.exe'
$skip = 'Jarvis Remote Control'

function Q([string]$s) { $s -replace '"', '""' }   # VBScript string escaping

$saved = @{}
if (Test-Path $save) {
    (Get-Content $save -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $saved[$_.Name] = $_.Value }
}

if ($env:JARVIS_UNHIDE -eq '1') {
    foreach ($name in @($saved.Keys)) {
        $o = $saved[$name]
        try {
            $a = if ($o.WorkingDirectory) { New-ScheduledTaskAction -Execute $o.Execute -Argument $o.Arguments -WorkingDirectory $o.WorkingDirectory }
                 else { New-ScheduledTaskAction -Execute $o.Execute -Argument $o.Arguments }
            Set-ScheduledTask -TaskName $name -Action $a -ErrorAction Stop | Out-Null
            Write-Host "restored  $name" -ForegroundColor Green
        } catch { Write-Host "FAILED    $name : $($_.Exception.Message)" -ForegroundColor Red }
    }
    return
}

$tasks = Get-ScheduledTask -TaskName 'Jarvis*' -ErrorAction SilentlyContinue
foreach ($t in $tasks) {
    $name = $t.TaskName
    $acts = @($t.Actions)
    if ($name -eq $skip) { Write-Host "skipped   $name (Remote Control keeps its console)" -ForegroundColor DarkGray; continue }
    if ($acts.Count -ne 1 -or -not $acts[0].Execute) { Write-Host "skipped   $name (not a single program action)" -ForegroundColor DarkGray; continue }
    $a = $acts[0]
    $exe = [Environment]::ExpandEnvironmentVariables($a.Execute.Trim('"'))
    if ($consoleExes -notcontains (($exe -split "[\\/]")[-1]).ToLower()) {
        Write-Host "skipped   $name (runs $(($exe -split "[\\/]")[-1]), no console)" -ForegroundColor DarkGray; continue
    }
    $safeName = $name -replace '[^\w\- ]', '_'
    $vbs = Join-Path $dir "$safeName.vbs"
    $cmdLine = '"' + $exe + '"' + $(if ($a.Arguments) { ' ' + $a.Arguments } else { '' })
    $lines = @(
        "' Written by hide-task-windows.ps1 for the '$name' task: runs its command with no window and waits.",
        'Set sh = CreateObject("WScript.Shell")'
    )
    if ($a.WorkingDirectory) { $lines += "sh.CurrentDirectory = ""$(Q $a.WorkingDirectory)""" }
    $lines += "WScript.Quit sh.Run(""$(Q $cmdLine)"", 0, True)"
    Set-Content -Path $vbs -Value $lines -Encoding ASCII
    try {
        $new = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument "//B //Nologo `"$vbs`""
        Set-ScheduledTask -TaskName $name -TaskPath $t.TaskPath -Action $new -ErrorAction Stop | Out-Null
        if (-not $saved.ContainsKey($name)) {
            $saved[$name] = @{ Execute = $a.Execute; Arguments = $a.Arguments; WorkingDirectory = $a.WorkingDirectory }
        }
        Write-Host "hidden    $name" -ForegroundColor Green
    } catch {
        Write-Host "FAILED    $name : $($_.Exception.Message) (needs an admin PowerShell)" -ForegroundColor Yellow
    }
}
$saved | ConvertTo-Json -Depth 4 | Set-Content -Path $save -Encoding UTF8
Write-Host "Originals saved in $save"
