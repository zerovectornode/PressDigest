#!/usr/bin/env bash
# Run from your laptop for every update after the VM's initial setup.sh
# has run once. Idempotent - safe to re-run.
#
# Usage: deploy/deploy.sh <user>@<vm-ip-or-host>
#
# Builds the frontend locally first: the e2-micro has 1GB RAM and `npm
# run build` (webpack/esbuild/rollup all buffer a lot in memory) would
# likely OOM there - see design/DESIGN.md "Deployment: GCP e2-micro VM".
# Only the built frontend/dist/ is shipped, never frontend/node_modules
# or the frontend source needing its own build step on the VM.
set -euo pipefail

TARGET="${1:?usage: deploy.sh <user>@<host>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_APP_DIR="/opt/pressdigest/app"

echo "==> Building frontend locally"
(cd "$REPO_ROOT/frontend" && npm ci && npm run build)

echo "==> Syncing app code + built frontend to $TARGET:$REMOTE_APP_DIR"
# --rsync-path="sudo rsync": runs the *remote* side of rsync as root, so
# it can write into a directory root owns without also needing the
# deploying user to be a member of any special group on the VM - see
# setup.sh for why root ownership was chosen for everything except data/.
# data/ is excluded here specifically so this never touches (or reverts
# the ownership of) the one directory the running service writes to.
rsync -az --delete --rsync-path="sudo rsync" \
  --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.claude' \
  --exclude 'data' \
  --exclude 'docs' \
  --exclude 'tests' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/src' --exclude 'frontend/public' \
  --exclude 'frontend/*.json' --exclude 'frontend/*.ts' --exclude 'frontend/*.html' \
  "$REPO_ROOT/" "$TARGET:$REMOTE_APP_DIR/"

echo "==> Installing/updating Python deps and restarting service"
ssh "$TARGET" "sudo /opt/pressdigest/venv/bin/pip install --no-cache-dir -e '$REMOTE_APP_DIR[api]' \
  && sudo systemctl restart pressdigest \
  && sleep 1 \
  && sudo systemctl status pressdigest --no-pager -l \
  && curl -sf http://127.0.0.1:8000/api/health && echo"

echo
echo "==> Done. Tail logs with: ssh $TARGET 'sudo journalctl -u pressdigest -f'"
