#!/usr/bin/env bash
# install.sh — скачать архив dnotool с GitHub, распаковать и запустить установку
# Запуск: bash <(curl -sL -H "Authorization: token ТОКЕН" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/xp9k/dno-tool/contents/scripts/install.sh)

set -euo pipefail

GITHUB_TOKEN="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"

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"

echo "=== Установка dnotool ==="

echo "Получение информации о последнем релизе..."

api_get() {
    curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" "$1"
}

LATEST_TAG=""
ALL_TAGS=$(api_get "https://api.github.com/repos/${REPO}/releases" | grep '"tag_name"' | grep -oP '"v\K[0-9]+\.[0-9]+\.[0-9]+"' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)

if [ -z "${ALL_TAGS}" ]; then
    echo "Ошибка: не удалось получить список релизов."
    exit 1
fi

LATEST_TAG="v${ALL_TAGS}"
LATEST_VERSION="${ALL_TAGS}"

echo "Последняя версия: ${LATEST_VERSION}"

ARCHIVE_NAME="${BINARY_NAME}-${LATEST_VERSION}-mos.zip"

echo "Загрузка ${ARCHIVE_NAME}..."

TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

api_get "https://api.github.com/repos/${REPO}/releases/tags/${LATEST_TAG}" > "${TMPDIR}/release.json"

ASSET_ID=$(grep -A2 "\"name\": \"${ARCHIVE_NAME}\"" "${TMPDIR}/release.json" | grep '"id"' | head -1 | grep -oP '\d+')

if [ -z "${ASSET_ID}" ]; then
    echo "Ошибка: архив ${ARCHIVE_NAME} не найден."
    cat "${TMPDIR}/release.json"
    exit 1
fi

curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "Accept: application/octet-stream" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${ARCHIVE_NAME}" "https://api.github.com/repos/${REPO}/releases/assets/${ASSET_ID}"

echo "Распаковка..."
unzip -o "${TMPDIR}/${ARCHIVE_NAME}" -d "${TMPDIR}/extracted" >/dev/null

cd "${TMPDIR}/extracted"

chmod +x dnotool install.sh uninstall.sh 2>/dev/null || true
if [ -d policykit ]; then
    chmod +x policykit/dnotool-admin 2>/dev/null || true
fi

echo "Запуск установки..."
bash install.sh