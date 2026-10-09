#!/usr/bin/env bash
# vendor_cli.sh — bundle the Cloud Foundry CLI (cf v8), the MTA build tool (mbt)
# and the multiapps plugin (provides `cf deploy`) as LINUX binaries into ./bin,
# so the hosted generator can deploy generated apps from inside its CF
# container (which has no CLI of its own).
#
# Run ONCE before `cf push` (works from Linux, macOS or Git Bash on Windows —
# nothing here executes the downloaded binaries). ./bin then uploads with the
# app. The multiapps plugin is registered at container START (see the start
# command in manifest.yml), because registering requires running the Linux cf.
#
# Note: mbt drives GNU make, which the CF Python container lacks — the
# generator falls back to its built-in Python MTA builder there
# (deployer.build_mtar_python), so mbt is optional at runtime.
#
# SECURITY NOTE: in-container deploy means users of the hosted generator type
# their BTP credentials (prefer one-time SSO passcodes) into the Deploy tab;
# they pass through the server process. Keep the APP_ACCESS_PASSWORD gate on.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$ROOT/bin"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN_DIR"

CF_VERSION="${CF_VERSION:-8.7.11}"
MBT_VERSION="${MBT_VERSION:-1.2.49}"
MTA_PLUGIN_URL="${MTA_PLUGIN_URL:-https://github.com/cloudfoundry/multiapps-cli-plugin/releases/latest/download/multiapps-plugin.linux64}"

echo "Downloading cf CLI v${CF_VERSION} (linux64)..."
curl -fsSL "https://packages.cloudfoundry.org/stable?release=linux64-binary&version=${CF_VERSION}&source=github-rel" \
  -o "$TMP/cf.tgz"
# extract only the real binary — the archive's `cf -> cf8` symlink can't be
# created on Windows/Git Bash
tar -xzf "$TMP/cf.tgz" -C "$TMP" cf8 2>/dev/null || tar -xzf "$TMP/cf.tgz" -C "$TMP" cf
if [ -f "$TMP/cf8" ]; then cp "$TMP/cf8" "$BIN_DIR/cf"; else cp "$TMP/cf" "$BIN_DIR/cf"; fi

echo "Downloading mbt v${MBT_VERSION} (linux amd64)..."
curl -fsSL "https://github.com/SAP/cloud-mta-build-tool/releases/download/v${MBT_VERSION}/cloud-mta-build-tool_${MBT_VERSION}_Linux_amd64.tar.gz" \
  -o "$TMP/mbt.tgz"
tar -xzf "$TMP/mbt.tgz" -C "$TMP" mbt
cp "$TMP/mbt" "$BIN_DIR/mbt"

echo "Downloading multiapps plugin (linux64)..."
curl -fsSL "$MTA_PLUGIN_URL" -o "$BIN_DIR/multiapps-plugin.linux64"

chmod +x "$BIN_DIR/cf" "$BIN_DIR/mbt" "$BIN_DIR/multiapps-plugin.linux64" 2>/dev/null || true

echo "Vendored into $BIN_DIR:"
ls -la "$BIN_DIR"
echo "Done. Now run: cf push   (./bin uploads with the app; the plugin registers at start)"
