# install.ps1 — dnotool installer for Windows
# Installs binary and commands.json

$Token = "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"

$Repo = "xp9k/dno-tool"
$BinaryName = "dnotool"
$InstallDir = "${env:ProgramFiles}\dnotool"
$ConfigDir = "${env:USERPROFILE}\.dnotool"

Write-Host "=== dnotool installer ===" -ForegroundColor Cyan

$CurrentVersion = ""
try {
    $existingExe = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($existingExe) {
        $CurrentVersion = & $existingExe.Source --version 2>$null
        Write-Host "Current version: $CurrentVersion"
    }
} catch {}

Write-Host "Fetching latest release info..."

$Headers = @{
    "User-Agent" = "dnotool-updater"
    "Authorization" = "token $Token"
}

$ApiUrl = "https://api.github.com/repos/$Repo/releases/latest"
try {
    $Release = Invoke-RestMethod -Uri $ApiUrl -Headers $Headers -ErrorAction Stop
} catch {
    Write-Host "Error: Could not fetch release info: $_" -ForegroundColor Red
    exit 1
}

$LatestTag = $Release.tag_name
$LatestVersion = $LatestTag.TrimStart("v")

if ($LatestVersion -eq "latest" -or $LatestVersion -notmatch '^\d+\.\d+\.\d+$') {
    $AllReleases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -Headers $Headers
    $VersionReleases = $AllReleases | Where-Object { $_.tag_name -match '^v\d+\.\d+\.\d+$' }
    $Sorted = $VersionReleases | Sort-Object { [version]($_.tag_name.TrimStart('v')) }
    $LatestRelease = $Sorted[-1]
    $LatestVersion = $LatestRelease.tag_name.TrimStart('v')
    $Release = $LatestRelease
}

Write-Host "Latest version: $LatestVersion"

if ($CurrentVersion -eq $LatestVersion) {
    Write-Host "Already up to date ($LatestVersion). No update needed." -ForegroundColor Green
    exit 0
}

$ArchiveName = "$BinaryName-$LatestVersion-windows.zip"
$Asset = $Release.assets | Where-Object { $_.name -eq $ArchiveName } | Select-Object -First 1

if (-not $Asset) {
    Write-Host "Error: Could not find $ArchiveName in the release assets." -ForegroundColor Red
    exit 1
}

$DownloadUrl = $Asset.url

$TempDir = Join-Path $env:TEMP "dnotool-install-$(Get-Random)"
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

Write-Host "Downloading $ArchiveName..."
$ZipPath = Join-Path $TempDir $ArchiveName

try {
    Invoke-WebRequest -Uri $DownloadUrl -Headers @{
        "Authorization" = "token $Token"
        "Accept" = "application/octet-stream"
        "User-Agent" = "dnotool-updater"
    } -OutFile $ZipPath
} catch {
    Write-Host "Error: Download failed: $_" -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir
    exit 1
}

Write-Host "Extracting..."
Expand-Archive -Path $ZipPath -DestinationPath "$TempDir\extracted" -Force

Write-Host "Installing to $InstallDir..."
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

Copy-Item "$TempDir\extracted\$BinaryName.exe" "$InstallDir\$BinaryName.exe" -Force

$envPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($envPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$envPath;$InstallDir", "Machine")
    Write-Host "Added $InstallDir to system PATH." -ForegroundColor Green
}

if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}

if (-not (Test-Path "$ConfigDir\commands.json")) {
    Copy-Item "$TempDir\extracted\commands.json" "$ConfigDir\commands.json" -Force
    Write-Host "Installed default commands.json to $ConfigDir\"
} else {
    Write-Host "commands.json already exists in $ConfigDir\, keeping current version."
}

Remove-Item -Recurse -Force $TempDir

Write-Host ""
Write-Host "=== dnotool $LatestVersion installed successfully! ===" -ForegroundColor Green
Write-Host "Binary: $InstallDir\$BinaryName.exe"
Write-Host "Config: $ConfigDir\"
Write-Host ""
Write-Host "NOTE: You may need to restart your terminal for PATH changes to take effect." -ForegroundColor Yellow