# Local secrets for docker compose

Create these two files on the host. Do not commit them.

```bash
umask 077
printf '%s' 'a-long-approval-secret' > approval_secret
printf '%s' 'a-long-passphrase' > passphrase
chmod 0400 approval_secret passphrase
```

Compose mounts them into the `wallet` service only, as `/run/secrets/approval_secret`
and `/run/secrets/passphrase`. The `walletmcp` user inside the container can
read them. An agent container must not mount this directory and must not
receive `WALLET_MCP_APPROVAL_SECRET`.
