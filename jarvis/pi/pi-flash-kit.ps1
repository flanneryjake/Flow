# Jarvis Pi flash kit: run on the rig. Gets everything ready, opens Raspberry Pi Imager for the one write, then
# finishes the drive on its own once it's plugged back in.
# In a normal (non-admin) PowerShell window on the rig, paste:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); if(!$t){$t=Read-Host 'GitHub token'; [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN',$t,'User')}; irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/pi/pi-flash-kit.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   1. installs Raspberry Pi Imager (winget) and downloads Raspberry Pi OS Lite 64-bit, checksum verified
#   2. makes an SSH key for the rig if it has none, and adds "ssh jarvis-pi" to the rig's SSH config
#   3. asks once for the Wi-Fi password (taken from the rig's saved Wi-Fi when it can) and a Tailscale auth key,
#      saved in %USERPROFILE%\.jarvis\pi-secrets.json on the rig only
#   4. builds the first-boot files (cloud-init user-data/network-config, firstboot.sh, the Jarvis agent)
#   5. opens Imager: you write the downloaded image to the M.2 drive, skipping Imager's customisation
#   6. when the drive comes back as "bootfs", copies the first-boot files onto it and ejects it
# Run it again any time: it skips what's done. -PrepareOnly stops before step 5 (for a Claude session on the rig).
# Values can come as parameters or env vars instead of prompts: PI_WIFI_SSID, PI_WIFI_PASSWORD, PI_TS_AUTHKEY,
# PI_MQTT_PASS.

param(
    [switch]$PrepareOnly,
    [string]$WifiSsid = $env:PI_WIFI_SSID,
    [string]$WifiPassword = $env:PI_WIFI_PASSWORD,
    [string]$TailscaleKey = $env:PI_TS_AUTHKEY,
    [string]$MqttPass = $env:PI_MQTT_PASS,
    [int]$WaitMinutes = 45
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$flowRefs = @('claude/eager-knuth-lakcxt', 'claude/project-thread-sf2tkg')   # agent files may still be on the HA branch
$piHost = 'jarvis-pi'
$piUser = 'jarvis'
$country = 'US'
$timezone = 'America/New_York'
$kitDir = 'C:\Jarvis\pi'
$stage = Join-Path $kitDir 'stage'
$secretFile = Join-Path $env:USERPROFILE '.jarvis\pi-secrets.json'
$imageUrl = 'https://downloads.raspberrypi.com/raspios_lite_arm64_latest'

function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }

# Flow is private, so files come through the GitHub API with this user's GITHUB_TASKS_TOKEN.
function Get-FlowFile([string]$path, [string]$out) {
    $tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
    $h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
    if ($tok) { $h.Authorization = "Bearer $tok" }
    foreach ($ref in $flowRefs) {
        try { Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/$path`?ref=$ref" -OutFile $out; return }
        catch { $last = $_ }
    }
    throw "Could not download jarvis/$path from Flow ($last). Check that GITHUB_TASKS_TOKEN can read flanneryjake/Flow."
}

# Linux reads these files: UTF-8 without BOM, LF line endings.
function Write-LinuxFile([string]$path, [string]$text) {
    [IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
}

function Quote-Sh([string]$v) { "'" + ($v -replace "'", "'\''") + "'" }
function Quote-Yaml([string]$v) { '"' + ($v -replace '\\', '\\' -replace '"', '\"') + '"' }

function New-Password {
    $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'.ToCharArray()
    $bytes = New-Object byte[] 20
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

New-Item -ItemType Directory -Force -Path $kitDir, $stage, (Join-Path $stage 'jarvis'), (Split-Path $secretFile) | Out-Null

# --- 1. Imager + image -------------------------------------------------------------------
$imager = @("$env:ProgramFiles\Raspberry Pi Imager\rpi-imager.exe", "${env:ProgramFiles(x86)}\Raspberry Pi Imager\rpi-imager.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $imager) {
    Say 'Installing Raspberry Pi Imager...'
    winget install --id RaspberryPiFoundation.RaspberryPiImager -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    $imager = @("$env:ProgramFiles\Raspberry Pi Imager\rpi-imager.exe", "${env:ProgramFiles(x86)}\Raspberry Pi Imager\rpi-imager.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $imager) { Say 'Imager did not install through winget. Get it from raspberrypi.com/software.' 'Yellow' }
}

# The "latest" link redirects to the dated file; the checksum sits next to it as <file>.sha256.
$req = [Net.HttpWebRequest]::Create($imageUrl); $req.Method = 'HEAD'; $req.AllowAutoRedirect = $true
$resp = $req.GetResponse(); $realUrl = $resp.ResponseUri.AbsoluteUri; $resp.Close()
$imageFile = Join-Path $kitDir ([IO.Path]::GetFileName(([Uri]$realUrl).AbsolutePath))
$wantSha = ((Invoke-WebRequest -UseBasicParsing "$realUrl.sha256").Content -split '\s+')[0].ToLower()
if ((Test-Path $imageFile) -and ((Get-FileHash $imageFile -Algorithm SHA256).Hash.ToLower() -eq $wantSha)) {
    Say "Image already downloaded: $imageFile" 'Green'
} else {
    Say "Downloading $realUrl ..."
    Invoke-WebRequest -UseBasicParsing $realUrl -OutFile $imageFile
    if ((Get-FileHash $imageFile -Algorithm SHA256).Hash.ToLower() -ne $wantSha) { Remove-Item $imageFile; throw 'Image checksum mismatch; run again.' }
    Say "Image downloaded and verified: $imageFile" 'Green'
}
Get-ChildItem $kitDir -Filter '*raspios*.img.xz' | Where-Object FullName -ne $imageFile | Remove-Item -ErrorAction SilentlyContinue

# --- 2. SSH key and "ssh jarvis-pi" ------------------------------------------------------
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$keyPath = Join-Path $sshDir 'id_ed25519'
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
if (-not (Test-Path "$keyPath.pub")) {
    Say 'Making an SSH key for the rig...'
    cmd.exe /c "ssh-keygen -t ed25519 -q -N `"`" -C `"rig-$env:COMPUTERNAME`" -f `"$keyPath`""
}
$pubKeys = @((Get-Content "$keyPath.pub" -Raw).Trim())
$extraKeys = Join-Path $kitDir 'authorized_keys'   # drop more public keys here (homebase, laptop) before running
if (Test-Path $extraKeys) { $pubKeys += Get-Content $extraKeys | Where-Object { $_ -match '^ssh-' } }
$sshConfig = Join-Path $sshDir 'config'
if (-not ((Test-Path $sshConfig) -and (Select-String -Path $sshConfig -Pattern "^Host $piHost\b" -Quiet))) {
    Add-Content -Path $sshConfig -Value "`nHost $piHost`n    HostName $piHost`n    User $piUser`n    IdentityFile $keyPath`n"
}

# --- 3. Secrets (asked once, kept on the rig) --------------------------------------------
$s = if (Test-Path $secretFile) { Get-Content $secretFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
function Set-Secret($name, $value) { if ($value) { $s | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force } }
Set-Secret WifiSsid $WifiSsid; Set-Secret WifiPassword $WifiPassword; Set-Secret TailscaleKey $TailscaleKey; Set-Secret MqttPass $MqttPass
$interactive = [Environment]::UserInteractive -and -not $PrepareOnly

if (-not $s.WifiSsid) {
    # Use the Wi-Fi the rig is on, or its saved profile, when there is one.
    $ssid = (netsh wlan show interfaces 2>$null | Select-String '^\s+SSID\s+:\s+(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
    if ($ssid) {
        $key = (netsh wlan show profile name="$ssid" key=clear 2>$null | Select-String 'Key Content\s+:\s+(.+)$').Matches.Groups[1].Value
        if ($key) { Set-Secret WifiSsid $ssid.Trim(); Set-Secret WifiPassword $key.Trim(); Say "Using the rig's Wi-Fi: $ssid" 'Green' }
    }
}
if (-not $s.WifiSsid -and $interactive) {
    $ssid = Read-Host 'Wi-Fi name for the Pi (leave blank if it will use an Ethernet cable)'
    if ($ssid) { Set-Secret WifiSsid $ssid; Set-Secret WifiPassword (Read-Host "Password for $ssid") }
}
if (-not $s.TailscaleKey -and $interactive) {
    Say 'Tailscale auth key: login.tailscale.com/admin/settings/keys > Generate auth key (one-off, Pre-approved on).'
    Set-Secret TailscaleKey (Read-Host 'Paste the tskey-auth-... key (blank to skip)')
}
if (-not $s.MqttPass -and $interactive) {
    Set-Secret MqttPass (Read-Host 'MQTT password for user "jarvis" on homebase (blank to skip; Claude can add it later)')
}
if (-not $s.PiPassword) { Set-Secret PiPassword (New-Password) }
$s | ConvertTo-Json | Set-Content -Path $secretFile -Encoding UTF8

# --- 4. First-boot files -----------------------------------------------------------------
Get-FlowFile 'pi/firstboot.sh' (Join-Path $stage 'jarvis\firstboot.sh')
Get-FlowFile 'homeassistant/linux-agent/jarvis_agent.py' (Join-Path $stage 'jarvis\jarvis_agent.py')
Get-FlowFile 'homeassistant/linux-agent/jarvis-agent.service' (Join-Path $stage 'jarvis\jarvis-agent.service')

$keysYaml = ($pubKeys | ForEach-Object { "      - $_" }) -join "`n"
Write-LinuxFile (Join-Path $stage 'user-data') @"
#cloud-config
hostname: $piHost
manage_etc_hosts: true
timezone: $timezone
keyboard:
  layout: us
users:
  - name: $piUser
    groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
    shell: /bin/bash
    lock_passwd: false
    ssh_authorized_keys:
$keysYaml
    sudo: ALL=(ALL) NOPASSWD:ALL
enable_ssh: true
ssh_pwauth: false
runcmd:
  - [bash, -c, "tr -d '\\r' < /boot/firmware/jarvis/firstboot.sh > /root/jarvis-firstboot.sh && bash /root/jarvis-firstboot.sh"]

"@

$net = @"
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    eth0:
      dhcp4: true
      optional: true
"@
if ($s.WifiSsid) {
    $net += @"

  wifis:
    wlan0:
      dhcp4: true
      optional: true
      regulatory-domain: "$country"
      access-points:
        $(Quote-Yaml $s.WifiSsid):
          password: $(Quote-Yaml $s.WifiPassword)
"@
}
Write-LinuxFile (Join-Path $stage 'network-config') ($net + "`n")
Write-LinuxFile (Join-Path $stage 'meta-data') "instance-id: $piHost-$(Get-Date -Format yyyyMMddHHmmss)`n"
$envLines = @("JARVIS_USER=$(Quote-Sh $piUser)", "PI_HOSTNAME=$(Quote-Sh $piHost)", "PI_PASSWORD=$(Quote-Sh $s.PiPassword)")
if ($s.TailscaleKey) { $envLines += "TS_AUTHKEY=$(Quote-Sh $s.TailscaleKey)" }
if ($s.MqttPass) { $envLines += "MQTT_PASS=$(Quote-Sh $s.MqttPass)" }
Write-LinuxFile (Join-Path $stage 'jarvis\secrets.env') (($envLines -join "`n") + "`n")
Say "First-boot files ready in $stage" 'Green'

if ($PrepareOnly) { Say 'Prepared. Run again without -PrepareOnly at the rig to flash.' 'Green'; return }

# --- 5. Imager ---------------------------------------------------------------------------
function Find-BootFs {
    Get-Volume | Where-Object { $_.FileSystemLabel -eq 'bootfs' -and $_.DriveLetter } | ForEach-Object {
        $root = "$($_.DriveLetter):\"
        # Only a USB/SD disk; if Windows won't say the bus type, the label and cmdline.txt are enough.
        $bus = try { (Get-Partition -DriveLetter $_.DriveLetter | Get-Disk).BusType } catch { 'USB' }
        if ($bus -in 'USB', 'SD', 'MMC' -and (Test-Path (Join-Path $root 'cmdline.txt'))) { $root }
    } | Select-Object -First 1
}

$boot = Find-BootFs
$fresh = $boot -and -not (Test-Path (Join-Path $boot 'jarvis\KIT-INSTALLED.txt'))
if (-not $fresh) {
    Say ''
    Say 'Now write the image with Raspberry Pi Imager:' 'Yellow'
    Say "  Device: your Pi model.  OS: scroll down > Use custom > $imageFile" 'Yellow'
    Say '  Storage: the 128 GB drive (check the size!).  Customisation: skip it / No.  Then Write and Yes.' 'Yellow'
    Say '  When Imager says done, unplug the drive and plug it back in. This window finishes the rest.' 'Yellow'
    Say '  If Windows offers to format a disk, click Cancel (that is the Linux partition).' 'Yellow'
    if ($imager) { Start-Process $imager }
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    do {
        Start-Sleep -Seconds 5
        $boot = Find-BootFs
        $fresh = $boot -and -not (Test-Path (Join-Path $boot 'jarvis\KIT-INSTALLED.txt'))
    } until ($fresh -or (Get-Date) -gt $deadline)
    if (-not $fresh) { throw "No freshly written drive showed up in $WaitMinutes min. Run this again after flashing." }
    Start-Sleep -Seconds 3
}

# --- 6. Finish the drive -----------------------------------------------------------------
if (-not (Test-Path (Join-Path $boot 'meta-data'))) {
    throw "$boot has no cloud-init files, so it's an older image. Write the image this script downloaded ($imageFile)."
}
Say "Finishing the drive at $boot ..."
Copy-Item (Join-Path $stage 'user-data'), (Join-Path $stage 'network-config'), (Join-Path $stage 'meta-data') $boot -Force
New-Item -ItemType Directory -Force -Path (Join-Path $boot 'jarvis') | Out-Null
Copy-Item (Join-Path $stage 'jarvis\*') (Join-Path $boot 'jarvis') -Force

# Pi 5 on a non-5A supply limits USB power and may not start a USB drive; allow full USB current.
$cfg = Join-Path $boot 'config.txt'
if (-not (Select-String -Path $cfg -Pattern 'usb_max_current_enable' -Quiet)) {
    Add-Content -Path $cfg -Value "`n[pi5]`n# Jarvis: boot from the M.2 drive over USB`nusb_max_current_enable=1`n[all]`n"
}
Write-LinuxFile (Join-Path $boot 'jarvis\KIT-INSTALLED.txt') "Jarvis kit copied $(Get-Date -Format s) from $env:COMPUTERNAME`n"

$letter = $boot.Substring(0, 2)
try { (New-Object -ComObject Shell.Application).Namespace(17).ParseName($letter).InvokeVerb('Eject') } catch { }
Say ''
Say 'Done. The drive is ready.' 'Green'
Say '  1. Plug it into a BLUE USB 3 port on the Pi (the Pi''s USB-C port is power only), no SD card in.' 'Green'
Say '  2. Plug in Ethernet if you have it, then power. First boot takes about 10 minutes.' 'Green'
Say "  3. It joins the tailnet as $piHost. From the rig: ssh $piHost" 'Green'
Say "Pi console password is in $secretFile (PiPassword)." 'Green'
