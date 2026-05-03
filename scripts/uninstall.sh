#!/usr/bin/env bash
# Удаление dnotool

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$0" "$@"
fi

echo "=== Удаление dnotool ==="

rm -f /usr/local/bin/dnotool
rm -f /usr/bin/dnotool-admin
rm -f /usr/local/bin/dnotool-uninstall.sh
rm -f /usr/share/polkit-1/actions/com.dnotool.policy
rm -f /usr/share/applications/com.dnotool.pkexec.desktop
update-desktop-database /usr/share/applications/ 2>/dev/null || true

echo "=== dnotool удалён! ==="
echo "Конфигурация ~/.dnotool сохранена. Для удаления: rm -rf ~/.dnotool"