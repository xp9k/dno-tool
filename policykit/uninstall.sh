#!/usr/bin/env bash
# Uninstall dnotool

set -euo pipefail

echo "=== dnotool uninstaller ==="

rm -f /usr/local/bin/dnotool
rm -f /usr/bin/dnotool-admin
rm -f /usr/local/bin/dnotool-uninstall.sh
rm -f /usr/share/polkit-1/actions/com.dnotool.policy
rm -f /usr/share/applications/com.dnotool.pkexec.desktop
rm -f /usr/share/applications/com.dnotool.desktop

echo "=== dnotool uninstalled! ==="
echo "Config ~/.dnotool preserved. Remove manually: rm -rf ~/.dnotool"