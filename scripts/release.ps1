# release.ps1 — сборка бинарного файла, создание релиза на GitHub
# Требуется: gh CLI (авторизованный) и файл .env.tokens

$ErrorActionPreference = "Stop"

$ProjectRoot = if ($PSScriptRoot) { Join-Path $PSScriptRoot ".." } else { $PWD.Path }
Set-Location $ProjectRoot

if (-not (Test-Path ".env.tokens")) {
    Write-Host "Ошибка: файл .env.tokens не найден." -ForegroundColor Red
    exit 1
}

$GH_TOKEN = ""
foreach ($line in Get-Content ".env.tokens") {
    if ($line -match '^GITHUB_TOKEN_WRITE=(.+)$') {
        $GH_TOKEN = $Matches[1]
        break
    }
}
if (-not $GH_TOKEN) {
    Write-Host "Ошибка: GITHUB_TOKEN_WRITE не найден в .env.tokens" -ForegroundColor Red
    exit 1
}
$env:GH_TOKEN = $GH_TOKEN

$Repo = "xp9k/dno-tool"
$BinaryName = "dnotool"

$VersionLine = Select-String -Path "src\__init__.py" -Pattern "__version__" | Select-Object -First 1
$Version = ($VersionLine.Line -split '=')[1].Trim().Trim('"', "'")
$Tag = "v$Version"

Write-Host "=== Создание релиза $Tag ===" -ForegroundColor Cyan

where.exe gh >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Ошибка: gh CLI не найден. Установите: https://cli.github.com/" -ForegroundColor Red
    exit 1
}

Write-Host "Сборка бинарного файла Windows..."
& .venv\Scripts\Activate.ps1
pyinstaller dnotool.spec
if ($LASTEXITCODE -ne 0) { Write-Host "Ошибка сборки!" -ForegroundColor Red; exit 1 }

$DistDir = "dist"
$WinDir = Join-Path $env:TEMP "dnotool-win-$(Get-Random)"
New-Item -ItemType Directory -Path $WinDir -Force | Out-Null

Write-Host "Упаковка Windows-архива..."
Copy-Item "$DistDir\$BinaryName.exe" $WinDir
Copy-Item "commands.json" $WinDir
$WinArchive = "$DistDir\$BinaryName-$Version-windows.zip"
Compress-Archive -Path "$WinDir\*" -DestinationPath $WinArchive -Force

$HasMos = Test-Path "$DistDir\$BinaryName"
if ($HasMos) {
    Write-Host "Упаковка MOS-архива..."
    $MosDir = Join-Path $env:TEMP "dnotool-mos-$(Get-Random)"
    New-Item -ItemType Directory -Path $MosDir -Force | Out-Null
    New-Item -ItemType Directory -Path "$MosDir\policykit" -Force | Out-Null
    Copy-Item "$DistDir\$BinaryName" $MosDir
    Copy-Item "commands.json" $MosDir
    Copy-Item "scripts\install.sh" $MosDir
    Copy-Item "scripts\uninstall.sh" $MosDir
    Copy-Item "policykit\com.dnotool.policy" "$MosDir\policykit\"
    Copy-Item "policykit\com.dnotool.pkexec.desktop" "$MosDir\policykit\"
    Copy-Item "policykit\dnotool-admin" "$MosDir\policykit\"
    $desktop = Get-Content "policykit\com.dnotool.desktop"
    $desktop = $desktop -replace '^Version=.*', "Version=$Version"
    $desktop | Set-Content "$MosDir\policykit\com.dnotool.desktop"
    $MosArchive = "$DistDir\$BinaryName-$Version-mos.zip"
    Compress-Archive -Path "$MosDir\*" -DestinationPath $MosArchive -Force
    Write-Host "MOS-архив создан."
} else {
    Write-Host "ВНИМАНИЕ: Linux-бинарный файл не найден. MOS-архив пропущен." -ForegroundColor Yellow
}

Write-Host "Создание релиза $Tag на GitHub..."
if ($HasMos) {
    gh release create $Tag --repo $Repo --title $Tag --notes "Release $Tag" $MosArchive $WinArchive
} else {
    gh release create $Tag --repo $Repo --title $Tag --notes "Release $Tag" $WinArchive
}

Write-Host "Обновление тега latest..."
try { gh release delete latest --repo $Repo --yes 2>$null | Out-Null } catch { }
if ($HasMos) {
    gh release create latest --repo $Repo --title latest --notes "Latest release ($Tag)" $MosArchive $WinArchive
} else {
    gh release create latest --repo $Repo --title latest --notes "Latest release ($Tag)" $WinArchive
}

Remove-Item -Recurse -Force $WinDir
Write-Host "=== Релиз $Tag успешно создан! ===" -ForegroundColor Green