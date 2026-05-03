#!/usr/bin/env bash
# release.sh — build binary, create/ update GitHub Release
# Requires: gh CLI authenticated or GITHUB_TOKEN set

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env.tokens"
if [ ! -f "${ENV_FILE}" ]; then
    echo "Ошибка: файл ${ENV_FILE} не найден. Создайте его с GITHUB_TOKEN_WRITE=..."
    exit 1
fi
GH_TOKEN=$(grep "^GITHUB_TOKEN_WRITE=" "${ENV_FILE}" | head -1 | cut -d= -f2-)
export GH_TOKEN

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"

VERSION=$(grep '__version__' src/__init__.py | sed "s/.*=.*['\"]//;s/['\"]//")
TAG="v${VERSION}"

echo "=== Создание релиза ${TAG} ==="

if ! command -v gh &>/dev/null; then
    echo "Ошибка: gh CLI не найден. Установите: https://cli.github.com/"
    exit 1
fi

echo "Сборка бинарного файла Linux/MOS..."
source ./.venv/bin/activate
pyinstaller dnotool.spec

TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

echo "Упаковка MOS-архива..."
MOS_DIR="${TMPDIR}/mos_pack"
mkdir -p "${MOS_DIR}/policykit"
cp "./dist/${BINARY_NAME}" "${MOS_DIR}/"
cp commands.json "${MOS_DIR}/"
cp scripts/install.sh "${MOS_DIR}/"
cp scripts/uninstall.sh "${MOS_DIR}/"
cp -r policykit/* "${MOS_DIR}/policykit/"
sed "s/^Version=.*/Version=${VERSION}/" policykit/com.dnotool.desktop > "${MOS_DIR}/policykit/com.dnotool.desktop"

MOS_ARCHIVE="${TMPDIR}/${BINARY_NAME}-${VERSION}-mos.zip"
(cd "${MOS_DIR}" && zip -r "${MOS_ARCHIVE}" .)
mv "${MOS_ARCHIVE}" "./dist/"

echo "Упаковка Windows-архива..."
WIN_DIR="${TMPDIR}/win_pack"
mkdir -p "${WIN_DIR}"
if [ -f "./dist/${BINARY_NAME}.exe" ]; then
    cp "./dist/${BINARY_NAME}.exe" "${WIN_DIR}/"
    cp commands.json "${WIN_DIR}/"
else
    echo "ВНИМАНИЕ: ${BINARY_NAME}.exe не найден. Windows-архив пропущен."
fi
WIN_ARCHIVE="./dist/${BINARY_NAME}-${VERSION}-windows.zip"
if [ -f "./dist/${BINARY_NAME}.exe" ]; then
    (cd "${WIN_DIR}" && zip -r "${WIN_ARCHIVE}" .)
fi

echo "Создание релиза ${TAG} на GitHub..."
if [ -f "${WIN_ARCHIVE}" ]; then
    gh release create "${TAG}" \
        --repo "${REPO}" \
        --title "${TAG}" \
        --notes "Release ${TAG}" \
        "./dist/${BINARY_NAME}-${VERSION}-mos.zip" \
        "${WIN_ARCHIVE}"
else
    gh release create "${TAG}" \
        --repo "${REPO}" \
        --title "${TAG}" \
        --notes "Release ${TAG}" \
        "./dist/${BINARY_NAME}-${VERSION}-mos.zip"
fi

echo "Обновление тега latest..."
gh release delete latest --repo "${REPO}" --yes 2>/dev/null || true
if [ -f "${WIN_ARCHIVE}" ]; then
    gh release create latest \
        --repo "${REPO}" \
        --title "latest" \
        --notes "Latest release (${TAG})" \
        "./dist/${BINARY_NAME}-${VERSION}-mos.zip" \
        "${WIN_ARCHIVE}"
else
    gh release create latest \
        --repo "${REPO}" \
        --title "latest" \
        --notes "Latest release (${TAG})" \
        "./dist/${BINARY_NAME}-${VERSION}-mos.zip"
fi

echo "=== Релиз ${TAG} успешно создан! ==="