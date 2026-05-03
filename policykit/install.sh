#!/bin/bash
# install.sh — локальная установка dnotool из распакованного архива
# Запуск: bash install.sh (права запрашиваются автоматически)

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Запрос прав суперпользователя..."
    exec sudo bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.dnotool"

echo "=== Установка dnotool ==="

echo "Установка бинарного файла в /usr/local/bin..."
cp "${SCRIPT_DIR}/dnotool" /usr/local/bin/dnotool
chmod +x /usr/local/bin/dnotool

echo "Установка файлов PolicyKit..."
if [ -d "${SCRIPT_DIR}/policykit" ]; then
    cp "${SCRIPT_DIR}/policykit/com.dnotool.policy" /usr/share/polkit-1/actions/
    cp "${SCRIPT_DIR}/policykit/com.dnotool.pkexec.desktop" /usr/share/applications/
    cp "${SCRIPT_DIR}/policykit/dnotool-admin" /usr/bin/dnotool-admin
    chmod +x /usr/bin/dnotool-admin
    echo "  Готово."
else
    echo "  Папка policykit не найдена, пропуск."
fi

echo "Обновление базы desktop-файлов..."
update-desktop-database /usr/share/applications/ 2>/dev/null || true

echo "Установка commands.json..."
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
    cp "${SCRIPT_DIR}/commands.json" "${CONFIG_DIR}/commands.json"
    echo "  Установлен в ${CONFIG_DIR}/"
else
    echo "  Уже существует, сохранён текущий."
fi

echo "Установка скрипта удаления..."
if [ -f "${SCRIPT_DIR}/uninstall.sh" ]; then
    cp "${SCRIPT_DIR}/uninstall.sh" /usr/local/bin/dnotool-uninstall.sh
    chmod +x /usr/local/bin/dnotool-uninstall.sh
fi

echo ""
echo "=== Установка завершена! ==="
echo "Запуск из меню: DNOTool / DNOTool (Администратор)"
echo "Удаление: sudo dnotool-uninstall.sh"