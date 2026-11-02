# Hive — agent-in-a-box Docker image
#
# Runs the built-in agent harness (lib/core/agent-harness.py: plain
# python3 + provider APIs + the MCP HTTP tool surface) inside a container
# that only sees its assigned worktree.  .company/, the host filesystem,
# and everything else are inaccessible.
#
# Build:
#   docker build -t hive-agent .
#
# Run (agent spawn):
#   docker run --rm \
#     -e HIVE_MODEL="$HIVE_MODEL" \
#     -e HIVE_API_BASE="$HIVE_API_BASE" \
#     -e HIVE_API_KEY="$HIVE_API_KEY" \
#     -e MCP_URL="http://127.0.0.1:7891/mcp" \
#     -v "$WORKTREE_DIR:/work:rw" \
#     -w /work \
#     --tmpfs /root:noexec,nosuid,size=100m \
#     --network host \
#     hive-agent \
#     /entrypoint.sh "<prompt>"
#
# Security model:
#   • The container has NO access to .company/ or the host filesystem.
#     Only the worktree is mounted (read-write).
#   • The API key lives only in the container's environment — never on disk.
#   • /root (the agent's HOME / state dir) is a tmpfs — ephemeral, no host
#     leakage.
#   • --network host gives the agent access to the MCP server on port 7891
#     (for hive tool calls) and the LLM provider API (internet).
#
# Notes:
#   • The harness's only runtime dependency is httpx, installed into a
#     throwaway venv at /opt/hive/venv.
#   • No useradd/sudo needed — the container runs as root inside the
#     isolated filesystem, which is sufficient for sandboxing.

FROM debian:trixie-slim

# ca-certificates for HTTPS to the LLM provider API.
# python3 + python3-venv for the harness and its venv.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# The harness, plus a venv holding its single runtime dependency (httpx).
COPY lib/core/agent-harness.py /opt/hive/lib/core/agent-harness.py
RUN python3 -m venv /opt/hive/venv \
    && /opt/hive/venv/bin/pip install --no-cache-dir --quiet httpx

# Environment
ENV HOME=/root
ENV PATH="/opt/hive/venv/bin:$PATH"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Default: show harness help so the image is inspectable without an API key
CMD ["/opt/hive/venv/bin/python", "/opt/hive/lib/core/agent-harness.py", "--help"]
