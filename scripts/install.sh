#!/usr/bin/env bash
# install.sh — скачать dnotool с GitHub и установить
# Запуск: bash <(curl -sL https://raw.githubusercontent.com/xp9k/dno-tool/main/scripts/install.sh)

set -euo pipefail

REPO="xp9k/dno-tool"

echo "=== Установка dnotool ==="

echo "Получение информации о последнем релизе..."

TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

curl -sfL -H "User-Agent: dnotool-updater" "https://api.github.com/repos/${REPO}/releases" -o "${TMPDIR}/releases.json"

parse_json=$(python3 -c "
import json,sys
with open(sys.argv[1]) as f:
    data=json.load(f)
versions=[]
for r in data:
    tag=r['tag_name']
    if tag.startswith('v') and tag[1:].replace('.','').isdigit():
        versions.append((tag,r))
if not versions:
    print('ERROR:no_versioned_releases',file=sys.stderr)
    sys.exit(1)
versions.sort(key=lambda x:[int(p) for p in x[0][1:].split('.')])
tag,rel=versions[-1]
rpm_asset=None
commands_asset=None
for a in rel['assets']:
    if a['name'].endswith('.rpm') and rpm_asset is None:
        rpm_asset=a
    if 'commands' in a['name'] and a['name'].endswith('.zip') and commands_asset is None:
        commands_asset=a
if not rpm_asset:
    print('ERROR:no_rpm_asset',file=sys.stderr)
    sys.exit(1)
print(f'{tag[1:]}')
print(f'{rpm_asset[\"id\"]}')
print(f'{rpm_asset[\"name\"]}')
if commands_asset:
    print(f'{commands_asset[\"id\"]}')
    print(f'{commands_asset[\"name\"]}')
" "${TMPDIR}/releases.json")

if [ $? -ne 0 ]; then
    echo "Ошибка: не удалось найти релиз."
    exit 1
fi

LATEST_VERSION=$(echo "${parse_json}" | sed -n '1p')
RPM_ASSET_ID=$(echo "${parse_json}" | sed -n '2p')
RPM_NAME=$(echo "${parse_json}" | sed -n '3p')
COMMANDS_LINE4=$(echo "${parse_json}" | sed -n '4p')
COMMANDS_LINE5=$(echo "${parse_json}" | sed -n '5p')

echo "Последняя версия: ${LATEST_VERSION}"

echo "Загрузка ${RPM_NAME}..."
curl -fL --progress-bar -H "Accept: application/octet-stream" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${RPM_NAME}" "https://api.github.com/repos/${REPO}/releases/assets/${RPM_ASSET_ID}"

if [ -n "${COMMANDS_LINE4}" ] && [ -n "${COMMANDS_LINE5}" ]; then
    COMMANDS_ASSET_ID="${COMMANDS_LINE4}"
    COMMANDS_NAME="${COMMANDS_LINE5}"
    echo "Загрузка ${COMMANDS_NAME}..."
    curl -fL --progress-bar -H "Accept: application/octet-stream" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${COMMANDS_NAME}" "https://api.github.com/repos/${REPO}/releases/assets/${COMMANDS_ASSET_ID}"
fi

echo "Установка RPM-пакета..."
sudo dnf install -y "${TMPDIR}/${RPM_NAME}"

echo "Установка commands.json..."
CONFIG_DIR="${HOME}/.dnotool"
mkdir -p "${CONFIG_DIR}"
if [ -n "${COMMANDS_LINE4}" ] && [ -f "${TMPDIR}/${COMMANDS_NAME}" ]; then
    cd "${TMPDIR}"
    unzip -o "${COMMANDS_NAME}" -d "${TMPDIR}/commands_extract" >/dev/null 2>&1 || true
    if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
        cp "${TMPDIR}/commands_extract/commands.json" "${CONFIG_DIR}/commands.json" 2>/dev/null || true
        echo "  Установлен в ${CONFIG_DIR}/"
    else
        echo "  Уже существует, сохранён текущий."
    fi
fi

echo ""
echo "=== Установка завершена! ==="
echo "Запуск из меню: dno-tool"