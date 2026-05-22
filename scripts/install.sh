#!/usr/bin/env bash
# install.sh — скачать архив dnotool с GitHub, распаковать и запустить установку
# Запуск: bash <(curl -sL https://raw.githubusercontent.com/xp9k/dno-tool/main/scripts/install.sh)

set -euo pipefail

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"

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
mos_asset=None
for a in rel['assets']:
    if a['name'].endswith('-mos.zip'):
        mos_asset=a
        break
if not mos_asset:
    print('ERROR:no_mos_asset',file=sys.stderr)
    sys.exit(1)
print(f'{tag[1:]}')
print(f'{mos_asset[\"id\"]}')
print(f'{mos_asset[\"name\"]}')
" "${TMPDIR}/releases.json")

if [ $? -ne 0 ]; then
    echo "Ошибка: не удалось найти релиз."
    exit 1
fi

LATEST_VERSION=$(echo "${parse_json}" | sed -n '1p')
ASSET_ID=$(echo "${parse_json}" | sed -n '2p')
ARCHIVE_NAME=$(echo "${parse_json}" | sed -n '3p')

echo "Последняя версия: ${LATEST_VERSION}"

echo "Загрузка ${ARCHIVE_NAME}..."
curl -fL --progress-bar -H "Accept: application/octet-stream" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${ARCHIVE_NAME}" "https://api.github.com/repos/${REPO}/releases/assets/${ASSET_ID}"

echo "Распаковка..."
unzip -o "${TMPDIR}/${ARCHIVE_NAME}" -d "${TMPDIR}/extracted" >/dev/null

cd "${TMPDIR}/extracted"

chmod +x dnotool install.sh uninstall.sh 2>/dev/null || true
if [ -d policykit ]; then
    chmod +x policykit/dnotool-admin 2>/dev/null || true
fi

echo "Запуск установки..."
bash install.sh