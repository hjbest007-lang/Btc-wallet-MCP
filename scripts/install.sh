#!/bin/sh
# Install wallet-mcp under its own OS user.
#
# The approval secret is written to /etc/wallet-mcp/approval-secret mode 0400,
# owner walletmcp. The agent user is not that user and cannot read the file.
# The secret is never printed and never written into the repository.
#
#   sudo ./scripts/install.sh
#   ./scripts/install.sh --dry-run
set -eu

USER_NAME=walletmcp
SECRET_PATH=/etc/wallet-mcp/approval-secret
PASS_PATH=/etc/wallet-mcp/passphrase
DATA_DIR=/var/lib/wallet-mcp
UNIT_PATH=/etc/systemd/system/wallet-mcp.service
WRAPPER=/usr/local/bin/wallet-mcp-run

if [ "${1:-}" = "--dry-run" ]; then
  echo "user: ${USER_NAME}"
  echo "user_shell: /usr/sbin/nologin"
  echo "secret_path: ${SECRET_PATH}"
  echo "secret_mode: 0400"
  echo "secret_owner: ${USER_NAME}"
  echo "passphrase_path: ${PASS_PATH}"
  echo "passphrase_mode: 0400"
  echo "data_dir: ${DATA_DIR}"
  echo "data_mode: 0700"
  echo "unit_user: ${USER_NAME}"
  echo "unit_path: ${UNIT_PATH}"
  echo "wrapper: ${WRAPPER}"
  echo "mode: agent"
  echo "agent_cannot_read_secret: yes"
  echo "approval_secret_not_in_repo: yes"
  echo "approval_secret_not_printed: yes"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "refusing: run as root so the server user can be created, or pass --dry-run" >&2
  exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

install -d -m 0750 -o root -g "$USER_NAME" /etc/wallet-mcp
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$DATA_DIR"

write_secret() {
  # $1 path, $2 prompt. The value is not echoed and not logged.
  target=$1
  prompt=$2
  if [ -f "$target" ]; then
    return 0
  fi
  printf '%s' "$prompt"
  stty -echo
  IFS= read -r value
  stty echo
  printf '\n'
  if [ "${#value}" -lt 16 ]; then
    echo "refusing: secret must be at least 16 characters" >&2
    exit 1
  fi
  umask 077
  printf '%s' "$value" > "${target}.tmp"
  install -m 0400 -o "$USER_NAME" -g "$USER_NAME" "${target}.tmp" "$target"
  rm -f "${target}.tmp"
  unset value
}

write_secret "$SECRET_PATH" "Approval secret (not stored in the agent config): "
write_secret "$PASS_PATH" "Seed passphrase: "

# The wrapper runs as walletmcp, reads the 0400 files, and execs the server.
# It does not print either secret.
cat > "$WRAPPER" <<'EOF'
#!/bin/sh
set -eu
export WALLET_MCP_MODE=agent
export WALLET_MCP_DATA_DIR=/var/lib/wallet-mcp
export WALLET_MCP_APPROVAL_SECRET="$(cat /etc/wallet-mcp/approval-secret)"
export WALLET_MCP_PASSPHRASE="$(cat /etc/wallet-mcp/passphrase)"
exec wallet-mcp serve
EOF
chown root:"$USER_NAME" "$WRAPPER"
chmod 0750 "$WRAPPER"

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=wallet-mcp approval server
After=network.target

[Service]
Type=simple
User=${USER_NAME}
Group=${USER_NAME}
NoNewPrivileges=true
PrivateTmp=true
ExecStart=${WRAPPER}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$UNIT_PATH"

echo "installed. server user: ${USER_NAME}"
echo "secret: ${SECRET_PATH} (mode 0400, not readable by the agent user)"
echo "start with: systemctl enable --now wallet-mcp"
echo "The approval secret was not printed."
