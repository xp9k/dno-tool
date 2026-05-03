#!/usr/bin/env bash
# install.sh — dnotool installer for Linux/MOS
# Usage: bash install.sh
# Token for private repo access is embedded below.

set -euo pipefail

GITHUB_TOKEN="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"

REPO="xp9k/dno-tool"
BINARY_NAME="dnotool"
INSTALL_DIR="/usr/local/bin"
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

echo "Installing to ${INSTALL_DIR}..."
if [ -w "${INSTALL_DIR}" ]; then
    cp "${TMPDIR}/extracted/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
else
    echo "sudo required for ${INSTALL_DIR}:"
    sudo cp "${TMPDIR}/extracted/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    sudo chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
fi

if [ ! -d "${CONFIG_DIR}" ]; then
    mkdir -p "${CONFIG_DIR}"
fi

if [ ! -f "${CONFIG_DIR}/commands.json" ]; then
    cp "${TMPDIR}/extracted/commands.json" "${CONFIG_DIR}/commands.json"
    echo "Installed default commands.json to ${CONFIG_DIR}/"
else
    echo "commands.json already exists in ${CONFIG_DIR}/, keeping current version."
fi

echo ""
echo "=== dnotool ${LATEST_VERSION} installed successfully! ==="
echo "Binary: ${INSTALL_DIR}/${BINARY_NAME}"
echo "Config: ${CONFIG_DIR}/"