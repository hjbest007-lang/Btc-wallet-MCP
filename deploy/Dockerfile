FROM python:3.12-slim

# The server runs as its own user. The approval secret is not copied into the image.
RUN useradd --system --create-home --shell /usr/sbin/nologin --home-dir /var/lib/wallet-mcp walletmcp \
    && mkdir -p /var/lib/wallet-mcp /etc/wallet-mcp \
    && chown walletmcp:walletmcp /var/lib/wallet-mcp \
    && chmod 0700 /var/lib/wallet-mcp \
    && chown root:walletmcp /etc/wallet-mcp \
    && chmod 0750 /etc/wallet-mcp

WORKDIR /opt/wallet-mcp
COPY pyproject.toml README.md LICENSE DISCLAIMER.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && chown -R walletmcp:walletmcp /opt/wallet-mcp

COPY deploy/docker-entrypoint.sh /usr/local/bin/wallet-mcp-entrypoint
RUN chmod 0755 /usr/local/bin/wallet-mcp-entrypoint

USER walletmcp
ENV WALLET_MCP_MODE=agent \
    WALLET_MCP_DATA_DIR=/var/lib/wallet-mcp

ENTRYPOINT ["wallet-mcp-entrypoint"]
CMD ["serve"]
