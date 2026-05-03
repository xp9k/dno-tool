@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0.."

if not exist ".env.tokens" (
    echo Error: .env.tokens not found. Create it with GITHUB_TOKEN_WRITE=...
    exit /b 1
)
set "GH_TOKEN="
for /f "tokens=1,* delims==" %%a in ('findstr /B "GITHUB_TOKEN_WRITE=" .env.tokens') do set "GH_TOKEN=%%b"
if not defined GH_TOKEN (
    echo Error: GITHUB_TOKEN_WRITE not found in .env.tokens
    exit /b 1
)

set REPO=xp9k/dno-tool
set BINARY_NAME=dnotool

for /f "tokens=2 delims==" %%v in ('findstr /C:"__version__" src\__init__.py') do set VERSION_RAW=%%v
set VERSION=%VERSION_RAW: =%
set VERSION=%VERSION:"=%
set TAG=v%VERSION%

echo === Creating release %TAG% ===

where gh >nul 2>&1
if errorlevel 1 (
    echo Error: gh CLI not found. Install: https://cli.github.com/
    exit /b 1
)

echo Building Windows binary...
call .venv\Scripts\activate.bat
pyinstaller dnotool.spec

set DIST_DIR=dist
set TMP_DIR=%TEMP%\dnotool-release-%RANDOM%
mkdir "%TMP_DIR%"

echo Packing Windows archive...
set WIN_DIR=%TMP_DIR%\win_pack
mkdir "%WIN_DIR%"
copy "%DIST_DIR%\%BINARY_NAME%.exe" "%WIN_DIR%\" >nul
copy commands.json "%WIN_DIR%\" >nul
set WIN_ARCHIVE=%DIST_DIR%\%BINARY_NAME%-%VERSION%-windows.zip
powershell -Command "Compress-Archive -Path '%WIN_DIR%\*' -DestinationPath '%WIN_ARCHIVE%' -Force"

set HAS_MOS=0
if exist "%DIST_DIR%\%BINARY_NAME%" (
    set HAS_MOS=1
)

if "!HAS_MOS!"=="1" (
    echo Packing MOS archive...
    set MOS_DIR=%TMP_DIR%\mos_pack
    mkdir "!MOS_DIR!"
    mkdir "!MOS_DIR!\policykit"
    copy "%DIST_DIR%\%BINARY_NAME%" "!MOS_DIR!\" >nul
    copy commands.json "!MOS_DIR!\" >nul
    copy scripts\install.sh "!MOS_DIR!\" >nul
    copy scripts\uninstall.sh "!MOS_DIR!\" >nul
    copy policykit\com.dnotool.policy "!MOS_DIR!\policykit\" >nul
    copy policykit\com.dnotool.pkexec.desktop "!MOS_DIR!\policykit\" >nul
    copy policykit\dnotool-admin "!MOS_DIR!\policykit\" >nul
    powershell -Command "(Get-Content 'policykit\com.dnotool.desktop') -replace '^Version=.*', 'Version=%VERSION%' | Set-Content '!MOS_DIR!\policykit\com.dnotool.desktop'"
    set MOS_ARCHIVE=%DIST_DIR%\%BINARY_NAME%-%VERSION%-mos.zip
    powershell -Command "Compress-Archive -Path '!MOS_DIR!\*' -DestinationPath '!MOS_ARCHIVE!' -Force"
    echo MOS archive created.
) else (
    echo WARNING: Linux binary not found. MOS archive skipped.
)

echo Creating GitHub release %TAG%...
if "!HAS_MOS!"=="1" (
    gh release create %TAG% --repo %REPO% --title %TAG% --notes "Release %TAG%" "!MOS_ARCHIVE!" "%WIN_ARCHIVE%"
) else (
    gh release create %TAG% --repo %REPO% --title %TAG% --notes "Release %TAG%" "%WIN_ARCHIVE%"
)

echo Updating latest release tag...
gh release delete latest --repo %REPO% --yes 2>nul
if "!HAS_MOS!"=="1" (
    gh release create latest --repo %REPO% --title latest --notes "Latest release (%TAG%)" "!MOS_ARCHIVE!" "%WIN_ARCHIVE%"
) else (
    gh release create latest --repo %REPO% --title latest --notes "Latest release (%TAG%)" "%WIN_ARCHIVE%"
)

rmdir /s /q "%TMP_DIR%"
echo === Release %TAG% created successfully! ===