#!/bin/sh
# Load secrets from root-only files mounted into this container.
# The image itself does not contain WALLET_MCP_APPROVAL_SECRET.
# The process user is walletmcp. Do not mount these files into the agent container.
set -eu

if [ -n "${WALLET_MCP_APPROVAL_SECRET:-}" ] || [ -n "${WALLET_MCP_PASSPHRASE:-}" ]; then
  echo "refusing: pass secrets as files, not as environment variables on the container" >&2
  exit 1
fi

SECRET_FILE="${WALLET_MCP_APPROVAL_SECRET_FILE:-/run/secrets/approval_secret}"
PASS_FILE="${WALLET_MCP_PASSPHRASE_FILE:-/run/secrets/passphrase}"

if [ ! -f "$SECRET_FILE" ] || [ ! -f "$PASS_FILE" ]; then
  echo "refusing: approval secret and passphrase files are required" >&2
  exit 1
fi

# shellcheck disable=SC2091
WALLET_MCP_APPROVAL_SECRET="$(cat "$SECRET_FILE")"
WALLET_MCP_PASSPHRASE="$(cat "$PASS_FILE")"
export WALLET_MCP_APPROVAL_SECRET WALLET_MCP_PASSPHRASE
export WALLET_MCP_MODE="${WALLET_MCP_MODE:-agent}"
export WALLET_MCP_DATA_DIR="${WALLET_MCP_DATA_DIR:-/var/lib/wallet-mcp}"

exec wallet-mcp "$@"
