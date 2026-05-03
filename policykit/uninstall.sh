#!/usr/bin/env bash
# Удаление dnotool

set -euo pipefail

echo "=== Удаление dnotool ==="

rm -f /usr/local/bin/dnotool
rm -f /usr/bin/dnotool-admin
rm -f /usr/local/bin/dnotool-uninstall.sh
rm -f /usr/share/polkit-1/actions/com.dnotool.policy
rm -f /usr/share/applications/com.dnotool.pkexec.desktop
rm -f /usr/share/applications/com.dnotool.desktop

echo "=== dnotool удалён! ==="
echo "Конфигурация ~/.dnotool сохранена. Для удаления: rm -rf ~/.dnotool"