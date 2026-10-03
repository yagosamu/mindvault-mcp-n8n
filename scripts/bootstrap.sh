#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# bootstrap.sh — bring the MindVault stack up and make it usable.
#
# Starts n8n + Qdrant, waits for Qdrant, and creates the vector collection the
# workflows expect. Idempotent: running it twice is safe.
#
# Usage:
#   bash scripts/bootstrap.sh          # start everything
#   bash scripts/bootstrap.sh --down   # stop (keep data)
#   bash scripts/bootstrap.sh --nuke   # stop AND wipe volumes (reset)
#
# On Windows run this from Git Bash — it is a POSIX script.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

case "${1:-}" in
  --down)
    echo "▸ Stopping the stack (data preserved)..."
    docker compose down
    echo "  ✓ Stopped."
    exit 0 ;;
  --nuke)
    echo "▸ FULL RESET: stopping and wiping volumes..."
    docker compose down -v
    echo "  ✓ Wiped. n8n credentials and indexed vectors are gone."
    exit 0 ;;
  "" ) : ;;
  *  ) echo "✗ Unknown argument: $1  (use: --down | --nuke)"; exit 1 ;;
esac

# ── 1. Docker ────────────────────────────────────────────────────────────────
echo ""
echo "▸ Checking Docker..."
command -v docker >/dev/null 2>&1 || { echo "  ✗ docker not found in PATH."; exit 1; }
docker info >/dev/null 2>&1   || { echo "  ✗ Docker daemon not responding. Start Docker Desktop."; exit 1; }
echo "  ✓ Docker is running"

# ── 2. .env ──────────────────────────────────────────────────────────────────
echo ""
echo "▸ Checking .env..."
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "  ! Created .env from the template."
fi

# The compose file refuses to start without an encryption key, so generate one
# on first run rather than failing with a cryptic error.
if ! grep -qE '^N8N_ENCRYPTION_KEY=.+' .env; then
  if command -v openssl >/dev/null 2>&1; then
    KEY="$(openssl rand -hex 32)"
  else
    KEY="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  # Replace the empty assignment in place.
  sed -i.bak "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=${KEY}|" .env && rm -f .env.bak
  echo "  ✓ Generated N8N_ENCRYPTION_KEY (keep it: changing it orphans credentials)"
else
  echo "  ✓ N8N_ENCRYPTION_KEY present"
fi

set -a; source .env; set +a
QDRANT_PORT="${QDRANT_HOST_PORT:-6353}"
N8N_PORT="${N8N_HOST_PORT:-5678}"
COLLECTION="${QDRANT_COLLECTION:-mindvault}"

# ── 3. Up ────────────────────────────────────────────────────────────────────
echo ""
echo "▸ Starting containers..."
docker compose up -d

# ── 4. Wait for Qdrant ───────────────────────────────────────────────────────
echo ""
echo "▸ Waiting for Qdrant on :${QDRANT_PORT} (up to 60s)..."
ready=""
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:${QDRANT_PORT}/readyz" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[[ -n "$ready" ]] || { echo "  ✗ Qdrant did not become ready. Try: docker compose logs qdrant"; exit 1; }
echo "  ✓ Qdrant is ready"

# ── 5. Collection ────────────────────────────────────────────────────────────
# 1536 dimensions + Cosine — matches text-embedding-3-small. Changing the model
# means changing both, and re-indexing: vectors of different widths cannot mix.
echo ""
echo "▸ Ensuring collection '${COLLECTION}'..."
if curl -fsS "http://localhost:${QDRANT_PORT}/collections/${COLLECTION}" >/dev/null 2>&1; then
  echo "  ✓ Already exists"
else
  curl -fsS -X PUT "http://localhost:${QDRANT_PORT}/collections/${COLLECTION}" \
    -H 'Content-Type: application/json' \
    -d '{"vectors":{"size":1536,"distance":"Cosine"}}' >/dev/null
  echo "  ✓ Created (1536 dims, Cosine)"
fi

# ── banner ───────────────────────────────────────────────────────────────────
cat <<EOF

╔══════════════════════════════════════════════════════════════════╗
║   ✓ MINDVAULT READY                                              ║
╚══════════════════════════════════════════════════════════════════╝

  n8n editor .......... http://localhost:${N8N_PORT}
  Qdrant REST ......... http://localhost:${QDRANT_PORT}
  Qdrant dashboard .... http://localhost:${QDRANT_PORT}/dashboard
  Collection .......... ${COLLECTION} (1536 dims, Cosine)

  Inside n8n, point Qdrant nodes at:   http://qdrant:6333
  (the service name and INTERNAL port — containers talk over the
   compose network; localhost there means the n8n container itself)

  Next:
    1. open the editor, create the owner account (local, not n8n cloud)
    2. add credentials: OpenAI, GitHub
    3. build the workflows on the canvas, then export them to workflows/

  Control:
    bash scripts/bootstrap.sh --down   # stop, keep data
    bash scripts/bootstrap.sh --nuke   # reset everything
EOF
