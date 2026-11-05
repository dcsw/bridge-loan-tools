#!/usr/bin/env bash
# Install pinned dev tools (gitleaks, OpenTofu, Trivy) into ./.tools/bin (gitignored).
# Each archive is downloaded into its own temp dir and verified against the
# checksums file published with the same release before anything is extracted.
# Note: this verifies download integrity, not publisher authenticity (no signature check).
set -euo pipefail

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"
TOFU_VERSION="${TOFU_VERSION:-1.13.8}"
TRIVY_VERSION="${TRIVY_VERSION:-0.76.2}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/.tools/bin"
mkdir -p "$BIN"

fetch_verified() { # url checksums_url asset_name destdir
  local url="$1" sums_url="$2" asset="$3" dest="$4"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$dest/$asset" "$url"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$dest/SUMS" "$sums_url"
  local expected
  expected="$(awk -v a="$asset" '$2 == a || $2 == "*" a {print $1}' "$dest/SUMS")"
  [ -n "$expected" ] || { echo "no checksum entry for $asset" >&2; exit 1; }
  echo "$expected  $dest/$asset" | sha256sum --check --status \
    || { echo "checksum mismatch for $asset" >&2; exit 1; }
}

install_gitleaks() {
  local d; d="$(mktemp -d)"
  local a="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
  local base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
  fetch_verified "$base/$a" "$base/gitleaks_${GITLEAKS_VERSION}_checksums.txt" "$a" "$d"
  tar -xzf "$d/$a" -C "$d" gitleaks
  install -m 0755 "$d/gitleaks" "$BIN/gitleaks"
  rm -rf "$d"
}

install_tofu() {
  local d; d="$(mktemp -d)"
  local a="tofu_${TOFU_VERSION}_linux_amd64.zip"
  local base="https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}"
  fetch_verified "$base/$a" "$base/tofu_${TOFU_VERSION}_SHA256SUMS" "$a" "$d"
  unzip -q -o "$d/$a" tofu -d "$d"
  install -m 0755 "$d/tofu" "$BIN/tofu"
  rm -rf "$d"
}

install_trivy() {
  local d; d="$(mktemp -d)"
  local a="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
  local base="https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}"
  fetch_verified "$base/$a" "$base/trivy_${TRIVY_VERSION}_checksums.txt" "$a" "$d"
  tar -xzf "$d/$a" -C "$d" trivy
  install -m 0755 "$d/trivy" "$BIN/trivy"
  rm -rf "$d"
}

[ "$(uname -m)" = "x86_64" ] && [ "$(uname -s)" = "Linux" ] \
  || { echo "install_tools.sh supports linux/amd64 only" >&2; exit 1; }

install_gitleaks
install_tofu
install_trivy

echo "Installed into $BIN:"
"$BIN/gitleaks" version
"$BIN/tofu" version | head -1
"$BIN/trivy" --version | head -1
echo "Add to PATH:  export PATH=\"$BIN:\$PATH\""
