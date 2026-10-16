"""Installer and container layout. No root, no real secret, no broadcast."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_describes_a_separate_user_and_a_private_secret():
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "install.sh"), "--dry-run"],
        check=True, capture_output=True, text=True,
    )
    out = result.stdout
    assert "user: walletmcp" in out
    assert "user_shell: /usr/sbin/nologin" in out
    assert "secret_mode: 0400" in out
    assert "secret_owner: walletmcp" in out
    assert "data_mode: 0700" in out
    assert "unit_user: walletmcp" in out
    assert "mode: agent" in out
    assert "agent_cannot_read_secret: yes" in out
    assert "WALLET_MCP_APPROVAL_SECRET=" not in out
    assert "approval secret" not in out.lower() or "not_printed" in out


def test_installer_source_locks_down_the_secret():
    text = (ROOT / "scripts" / "install.sh").read_text()
    assert "useradd" in text
    assert "/usr/sbin/nologin" in text
    assert "0400" in text
    assert "stty -echo" in text
    assert "User=${USER_NAME}" in text
    assert "echo \"$value\"" not in text
    assert "printf '%s\\n' \"$value\"" not in text


def test_image_and_compose_keep_the_secret_out_of_the_agent():
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text()
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    entry = (ROOT / "deploy" / "docker-entrypoint.sh").read_text()
    assert "USER walletmcp" in dockerfile
    assert "WALLET_MCP_APPROVAL_SECRET" not in dockerfile
    assert "WALLET_MCP_APPROVAL_SECRET:" not in compose
    assert "approval_secret" in compose
    assert "user: \"walletmcp\"" in compose
    assert "pass secrets as files" in entry
    ignore = (ROOT / ".dockerignore").read_text()
    assert "deploy/secrets/approval_secret" in ignore
