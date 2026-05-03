#!/bin/bash
# install.sh — local install from extracted archive
# Usage: cd into extracted directory, then: sudo bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.dnotool"

echo "=== dnotool installer ==="

echo "Installing binary to /usr/local/bin..."
cp "${SCRIPT_DIR}/dnotool" /usr/local/bin/dnotool
chmod +x /usr/local/bin/dnotool

echo "Installing PolicyKit files..."
if [ -d "${SCRIPT_DIR}/policykit" ]; then
    cp "${SCRIPT_DIR}/policykit/com.dnotool.policy" /usr/share/polkit-1/actions/
    cp "${SCRIPT_DIR}/policykit/com.dnotool.pkexec.desktop" /usr/share/applications/
    cp "${SCRIPT_DIR}/policykit/com.dnotool.desktop" /usr/share/applications/
    cp "${SCRIPT_DIR}/policykit/dnotool-admin" /usr/bin/
    chmod +x /usr/bin/dnotool-admin
    echo "  Done."
fi

echo "Installing commands.json..."
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
    cp "${SCRIPT_DIR}/commands.json" "${CONFIG_DIR}/commands.json"
    echo "  Installed to ${CONFIG_DIR}/"
else
    echo "  Already exists, keeping current."
fi

echo "Installing uninstall script..."
if [ -f "${SCRIPT_DIR}/uninstall.sh" ]; then
    cp "${SCRIPT_DIR}/uninstall.sh" /usr/local/bin/dnotool-uninstall.sh
    chmod +x /usr/local/bin/dnotool-uninstall.sh
fi

echo ""
echo "=== Installation complete! ==="
echo "Run from menu: DNOTool / DNOTool (Administrator)"
echo "Uninstall: sudo dnotool-uninstall.sh"