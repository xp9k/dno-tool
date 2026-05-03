#!/usr/bin/env bash
# install.sh — dnotool installer for Linux/MOS
# Installs binary, policykit files, commands.json
# Usage: bash <(curl -sL https://raw.githubusercontent.com/xp9k/dno-tool/main/scripts/install.sh)

set -euo pipefail

GITHUB_TOKEN="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"

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"
CONFIG_DIR="${HOME}/.dnotool"

echo "=== dnotool installer ==="

CURRENT_VERSION=""
if command -v "${BINARY_NAME}" &>/dev/null; then
    CURRENT_VERSION=$("${BINARY_NAME}" --version 2>/dev/null || echo "")
    echo "Current version: ${CURRENT_VERSION:-unknown}"
fi

echo "Fetching latest release info..."

API_URL="https://api.github.com/repos/${REPO}/releases/latest"
RELEASE_DATA=$(curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" "${API_URL}")

if [ -z "${RELEASE_DATA}" ]; then
    echo "Error: Could not fetch release info."
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

echo "Latest version: ${LATEST_VERSION}"

if [ -n "${CURRENT_VERSION}" ] && [ "${CURRENT_VERSION}" = "${LATEST_VERSION}" ]; then
    echo "Already up to date (${LATEST_VERSION}). No update needed."
    exit 0
fi

ARCHIVE_NAME="${BINARY_NAME}-${LATEST_VERSION}-mos.zip"
DOWNLOAD_URL=$(echo "${RELEASE_DATA}" | grep '"browser_download_url"' | grep "${ARCHIVE_NAME}" | head -1 | sed 's/.*"browser_download_url"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/')

if [ -z "${DOWNLOAD_URL}" ]; then
    echo "Error: Could not find ${ARCHIVE_NAME} in the release assets."
    exit 1
fi

TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

echo "Downloading ${ARCHIVE_NAME}..."
curl -sfL -H "Authorization: token ${GITHUB_TOKEN}" -H "User-Agent: dnotool-updater" -o "${TMPDIR}/${ARCHIVE_NAME}" "${DOWNLOAD_URL}"

echo "Extracting..."
unzip -o "${TMPDIR}/${ARCHIVE_NAME}" -d "${TMPDIR}/extracted" >/dev/null

EXTRACTED="${TMPDIR}/extracted"

echo "Installing binary to /usr/local/bin..."
if [ -w /usr/local/bin ]; then
    cp "${EXTRACTED}/${BINARY_NAME}" /usr/local/bin/
    chmod +x /usr/local/bin/${BINARY_NAME}
else
    sudo cp "${EXTRACTED}/${BINARY_NAME}" /usr/local/bin/
    sudo chmod +x /usr/local/bin/${BINARY_NAME}
fi

echo "Installing PolicyKit files..."
POLICYKIT_DIR="${EXTRACTED}/policykit"
if [ -d "${POLICYKIT_DIR}" ]; then
    if [ -f "${POLICYKIT_DIR}/com.dnotool.policy" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.policy" /usr/share/polkit-1/actions/
        echo "  Polkit policy installed."
    fi
    if [ -f "${POLICYKIT_DIR}/com.dnotool.pkexec.desktop" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.pkexec.desktop" /usr/share/applications/
        echo "  Desktop shortcut (admin) installed."
    fi
    if [ -f "${POLICYKIT_DIR}/com.dnotool.desktop" ]; then
        sudo cp "${POLICYKIT_DIR}/com.dnotool.desktop" /usr/share/applications/
        echo "  Desktop shortcut installed."
    fi
    if [ -f "${POLICYKIT_DIR}/dnotool-admin" ]; then
        sudo cp "${POLICYKIT_DIR}/dnotool-admin" /usr/bin/
        sudo chmod +x /usr/bin/dnotool-admin
        echo "  Admin wrapper installed."
    fi
fi

echo "Installing commands.json..."
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
    cp "${EXTRACTED}/commands.json" "${CONFIG_DIR}/commands.json"
    echo "  Installed commands.json to ${CONFIG_DIR}/"
else
    echo "  commands.json already exists, keeping current."
fi

echo "Installing uninstall script..."
if [ -f "${EXTRACTED}/uninstall.sh" ]; then
    sudo cp "${EXTRACTED}/uninstall.sh" /usr/local/bin/dnotool-uninstall.sh
    sudo chmod +x /usr/local/bin/dnotool-uninstall.sh
    echo "  Uninstall script: dnotool-uninstall.sh"
fi

echo ""
echo "=== dnotool ${LATEST_VERSION} installed successfully! ==="
echo "Binary:   /usr/local/bin/dnotool"
echo "Config:   ${CONFIG_DIR}/"
echo "Polkit:   /usr/share/polkit-1/actions/com.dnotool.policy"
echo "To uninstall: sudo dnotool-uninstall.sh"