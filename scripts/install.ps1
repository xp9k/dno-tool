# install.ps1 — dnotool downloader for Windows
# Downloads the latest release archive to the Downloads folder

$Token = "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"

$Repo = "xp9k/dno-tool"
$BinaryName = "dnotool"

Write-Host "=== dnotool downloader ===" -ForegroundColor Cyan

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

$ArchiveName = "$BinaryName-$LatestVersion-windows.zip"
$Asset = $Release.assets | Where-Object { $_.name -eq $ArchiveName } | Select-Object -First 1

if (-not $Asset) {
    Write-Host "Error: Could not find $ArchiveName in the release assets." -ForegroundColor Red
    exit 1
}

$DownloadUrl = $Asset.url
$DownloadsDir = [Environment]::GetFolderPath("UserProfile") + "\Downloads"
$DestPath = Join-Path $DownloadsDir $ArchiveName

Write-Host "Downloading $ArchiveName to $DownloadsDir..."

try {
    Invoke-WebRequest -Uri $DownloadUrl -Headers @{
        "Authorization" = "token $Token"
        "Accept" = "application/octet-stream"
        "User-Agent" = "dnotool-updater"
    } -OutFile $DestPath
} catch {
    Write-Host "Error: Download failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Download complete! ===" -ForegroundColor Green
Write-Host "File saved: $DestPath"
Write-Host "Extract the archive and run dnotool.exe" -ForegroundColor Yellow

Start-Process "explorer.exe" $DownloadsDir