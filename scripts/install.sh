#!/usr/bin/env bash
# install.sh — установка dnotool для Linux/MOS
# Устанавливает бинарный файл, policykit, commands.json
# Запуск: bash <(curl -sL -H "Authorization: token ТОКЕН" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/xp9k/dno-tool/contents/scripts/install.sh)

set -euo pipefail

GITHUB_TOKEN="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"

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"
CONFIG_DIR="${HOME}/.dnotool"

echo "=== Установка dnotool ==="

CURRENT_VERSION=""
if command -v "${BINARY_NAME}" &>/dev/null; then
    CURRENT_VERSION=$("${BINARY_NAME}" --version 2>/dev/null || echo "")
    echo "Текущая версия: ${CURRENT_VERSION:-неизвестна}"
fi

echo "Получение информации о последнем релизе..."

API_URL="https://api.github.com/repos/${REPO}/releases/latest"
RELEASE_DATA=$(curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" "${API_URL}")

if [ -z "${RELEASE_DATA}" ]; then
    echo "Ошибка: не удалось получить информацию о релизе."
    exit 1
fi

LATEST_TAG=$(echo "${RELEASE_DATA}" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/')
LATEST_VERSION="${LATEST_TAG#v}"

if [[ "${LATEST_VERSION}" == "latest" || ! "${LATEST_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ALL_RELEASE_DATA=$(curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" "https://api.github.com/repos/${REPO}/releases")
    LATEST_TAG=$(echo "${ALL_RELEASE_DATA}" | grep '"tag_name"' | grep -oP '"v\K[0-9]+\.[0-9]+\.[0-9]+' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
    LATEST_VERSION="${LATEST_TAG#v}"
    RELEASE_DATA=$(echo "${ALL_RELEASE_DATA}" | grep -A5 "\"tag_name\": \"v${LATEST_VERSION}\"")
fi

echo "Последняя версия: ${LATEST_VERSION}"

if [ -n "${CURRENT_VERSION}" ] && [ "${CURRENT_VERSION}" = "${LATEST_VERSION}" ]; then
    echo "Установлена последняя версия (${LATEST_VERSION}). Обновление не требуется."
    exit 0
fi

ARCHIVE_NAME="${BINARY_NAME}-${LATEST_VERSION}-mos.zip"
ASSET_URL=$(echo "${RELEASE_DATA}" | grep '"url"' | grep -B1 "\"${ARCHIVE_NAME}\"" | grep '"url"' | head -1 | sed 's/.*"url"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/')
DOWNLOAD_URL=$(echo "${RELEASE_DATA}" | grep '"browser_download_url"' | grep "${ARCHIVE_NAME}" | head -1 | sed 's/.*"browser_download_url"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/')

if [ -z "${DOWNLOAD_URL}" ]; then
    echo "Ошибка: архив ${ARCHIVE_NAME} не найден в ресурсах релиза."
    exit 1
fi

TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

echo "Загрузка ${ARCHIVE_NAME}..."
if [ -n "${ASSET_URL}" ]; then
    curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "Accept: application/octet-stream" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${ARCHIVE_NAME}" "${ASSET_URL}"
else
    curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${ARCHIVE_NAME}" "${DOWNLOAD_URL}"
fi

echo "Распаковка..."
unzip -o "${TMPDIR}/${ARCHIVE_NAME}" -d "${TMPDIR}/extracted" >/dev/null

EXTRACTED="${TMPDIR}/extracted"

echo "Установка бинарного файла в /usr/local/bin..."
if [ -w /usr/local/bin ]; then
    cp "${EXTRACTED}/${BINARY_NAME}" /usr/local/bin/
    chmod +x /usr/local/bin/${BINARY_NAME}
else
    sudo cp "${EXTRACTED}/${BINARY_NAME}" /usr/local/bin/
    sudo chmod +x /usr/local/bin/${BINARY_NAME}
fi

echo "Установка файлов PolicyKit..."
POLICYKIT_DIR="${EXTRACTED}/policykit"
if [ -d "${POLICYKIT_DIR}" ]; then
    if [ -f "${POLICYKIT_DIR}/com.dnotool.policy" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.policy" /usr/share/polkit-1/actions/
        echo "  Политика Polkit установлена."
    fi
    if [ -f "${POLICYKIT_DIR}/com.dnotool.pkexec.desktop" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.pkexec.desktop" /usr/share/applications/
        echo "  Ярлык (администратор) установлен."
    fi
    if [ -f "${POLICYKIT_DIR}/com.dnotool.desktop" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.desktop" /usr/share/applications/
        echo "  Ярлык приложения установлен."
    fi
    if [ -f "${POLICYKIT_DIR}/dnotool-admin" ]; then
        sudo cp "${POLICYKIT_DIR}/dnotool-admin" /usr/bin/
        sudo chmod +x /usr/bin/dnotool-admin
        echo "  Обёртка администратора установлена."
    fi
fi

echo "Установка commands.json..."
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
    cp "${EXTRACTED}/commands.json" "${CONFIG_DIR}/commands.json"
    echo "  commands.json установлен в ${CONFIG_DIR}/"
else
    echo "  commands.json уже существует, сохранён текущий."
fi

echo "Установка скрипта удаления..."
if [ -f "${EXTRACTED}/uninstall.sh" ]; then
    sudo cp "${EXTRACTED}/uninstall.sh" /usr/local/bin/dnotool-uninstall.sh
    sudo chmod +x /usr/local/bin/dnotool-uninstall.sh
    echo "  Скрипт удаления: dnotool-uninstall.sh"
fi

echo ""
echo "=== dnotool ${LATEST_VERSION} успешно установлен! ==="
echo "Бинарный файл: /usr/local/bin/dnotool"
echo "Конфигурация:  ${CONFIG_DIR}/"
echo "Polkit:        /usr/share/polkit-1/actions/com.dnotool.policy"
echo "Удаление:      sudo dnotool-uninstall.sh"