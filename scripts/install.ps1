# install.ps1 — загрузчик dnotool для Windows
# Скачивает exe-архив с commands.json и распаковывает в папку "Загрузки\dnotool"

$Repo = "xp9k/dno-tool"
$BinaryName = "dnotool"

Write-Host "=== Загрузка dnotool ===" -ForegroundColor Cyan

Write-Host "Получение информации о последнем релизе..."

$Headers = @{
    "User-Agent" = "dnotool-updater"
}

$AllReleases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -Headers $Headers
$VersionReleases = $AllReleases | Where-Object { $_.tag_name -match '^v\d+\.\d+\.\d+$' }
$Sorted = $VersionReleases | Sort-Object { [version]($_.tag_name.TrimStart('v')) }
$Release = $Sorted[-1]
$LatestVersion = $Release.tag_name.TrimStart("v")

Write-Host "Последняя версия: $LatestVersion"

$DownloadsDir = [Environment]::GetFolderPath("UserProfile") + "\Downloads"

$WinArchive = "$BinaryName-$LatestVersion-windows.zip"
$WinAsset = $Release.assets | Where-Object { $_.name -eq $WinArchive } | Select-Object -First 1

if (-not $WinAsset) {
    Write-Host "Ошибка: архив $WinArchive не найден в ресурсах релиза." -ForegroundColor Red
    exit 1
}

$WinDestPath = Join-Path $DownloadsDir $WinArchive

Write-Host "Загрузка $WinArchive в папку $DownloadsDir..."

try {
    Invoke-WebRequest -Uri $WinAsset.url -Headers @{
        "Accept" = "application/octet-stream"
        "User-Agent" = "dnotool-updater"
    } -OutFile $WinDestPath
} catch {
    Write-Host "Ошибка: загрузка не удалась: $_" -ForegroundColor Red
    exit 1
}

$ExtractDir = Join-Path $DownloadsDir "dnotool"
if (Test-Path $ExtractDir) { Remove-Item -Recurse -Force $ExtractDir }

Write-Host "Распаковка $WinArchive..."
Expand-Archive -Path $WinDestPath -DestinationPath $ExtractDir -Force

Write-Host ""
Write-Host "=== Загрузка завершена! ===" -ForegroundColor Green
Write-Host "Файлы распакованы в: $ExtractDir"
Write-Host "Запустите dnotool.exe" -ForegroundColor Yellow

Start-Process "explorer.exe" $ExtractDir