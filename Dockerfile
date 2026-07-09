# Hive — claude-in-a-box Docker image
#
# Runs the Claude Code CLI inside a container that only sees its assigned
# worktree.  .company/, the host filesystem, and everything else are
# inaccessible.
#
# Build:
#   cp "$(which claude)" .
#   docker build -t hive-claude .
#
# Run (agent spawn):
#   docker run --rm \
#     -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
#     -e ANTHROPIC_BASE_URL="$ANTHROPIC_BASE_URL" \
#     -v "$WORKTREE_DIR:/work:rw" \
#     -w /work \
#     --tmpfs /root/.claude:noexec,nosuid,size=100m \
#     --network host \
#     hive-claude \
#     claude -p "<prompt>" --dangerously-skip-permissions \
#            --output-format stream-json --verbose
#
# Security model:
#   • The container has NO access to .company/ or the host filesystem.
#     Only the worktree is mounted (read-write).
#   • The API key lives only in the container's environment — never on disk.
#   • The claude cache (/root/.claude) is a tmpfs — ephemeral, no host leakage.
#   • --network host gives the agent access to the MCP server on port 7891
#     (for hive tool calls) and the Anthropic API (internet).
#
# Notes:
#   • The claude binary is ~245 MB. Total image size after build: ~320 MB.
#   • The binary is dynamically linked against glibc. Base image must provide
#     a compatible glibc (debian:trixie-slim has glibc 2.40).
#   • No useradd/sudo needed — the container runs as root inside the
#     isolated filesystem, which is sufficient for sandboxing.

FROM debian:trixie-slim

# ca-certificates for HTTPS to Anthropic API.
# curl for health-check / diagnostic use.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the claude CLI binary (placed in build context by the caller)
COPY claude /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Environment
ENV CLAUDE_HOME=/root/.claude
ENV HOME=/root

# Default: show help so the image is inspectable without an API key
CMD ["claude", "--help"]
