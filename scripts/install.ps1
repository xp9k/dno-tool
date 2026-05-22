# install.ps1 — загрузчик dnotool для Windows
# Скачивает архив последнего релиза в папку "Загрузки"

$Repo = "xp9k/dno-tool"
$BinaryName = "dnotool"

Write-Host "=== Загрузка dnotool ===" -ForegroundColor Cyan

Write-Host "Получение информации о последнем релизе..."

$Headers = @{
    "User-Agent" = "dnotool-updater"
}

$ApiUrl = "https://api.github.com/repos/$Repo/releases/latest"
try {
    $Release = Invoke-RestMethod -Uri $ApiUrl -Headers $Headers -ErrorAction Stop
} catch {
    Write-Host "Ошибка: не удалось получить информацию о релизе: $_" -ForegroundColor Red
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

Write-Host "Последняя версия: $LatestVersion"

$ArchiveName = "$BinaryName-$LatestVersion-windows.zip"
$Asset = $Release.assets | Where-Object { $_.name -eq $ArchiveName } | Select-Object -First 1

if (-not $Asset) {
    Write-Host "Ошибка: архив $ArchiveName не найден в ресурсах релиза." -ForegroundColor Red
    exit 1
}

$DownloadUrl = $Asset.url
$DownloadsDir = [Environment]::GetFolderPath("UserProfile") + "\Downloads"
$DestPath = Join-Path $DownloadsDir $ArchiveName

Write-Host "Загрузка $ArchiveName в папку $DownloadsDir..."

try {
    Invoke-WebRequest -Uri $DownloadUrl -Headers @{
        "Accept" = "application/octet-stream"
        "User-Agent" = "dnotool-updater"
    } -OutFile $DestPath
} catch {
    Write-Host "Ошибка: загрузка не удалась: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Загрузка завершена! ===" -ForegroundColor Green
Write-Host "Файл сохранён: $DestPath"
Write-Host "Распакуйте архив и запустите dnotool.exe" -ForegroundColor Yellow

Start-Process "explorer.exe" $DownloadsDir