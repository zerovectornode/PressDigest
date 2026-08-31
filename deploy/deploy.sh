#!/usr/bin/env bash
# Run from your laptop for every update after the VM's setup.sh has run
# once. Idempotent - safe to re-run.
#
# Usage: deploy/deploy.sh <user>@<vm-ip-or-host>
#
# Builds the frontend locally: the e2-micro has 1GB RAM and `npm run
# build` (webpack/esbuild/rollup all buffer heavily in memory) would
# likely OOM there - see design/DESIGN.md "Deployment: GCP e2-micro VM".
# Only frontend/dist/ is shipped; no Node is installed on the VM at all.
set -euo pipefail

TARGET="${1:?usage: deploy.sh <user>@<host>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_APP_DIR="/opt/pressdigest/app"

echo "==> Building frontend locally"
(cd "$REPO_ROOT/frontend" && npm ci && npm run build)

echo "==> Syncing app code + built frontend to $TARGET:$REMOTE_APP_DIR"
# --rsync-path="sudo rsync": runs the remote side as root, since the
# SSH-connecting user (your own gcloud/OS Login account) is a different
# identity from the pressdigest service user that owns /opt/pressdigest -
# root can write there regardless of ownership. The resulting app/ files
# end up root-owned, which is fine: pressdigest.service only needs to
# READ them (import over PYTHONPATH), never write to them - the venv
# (pip-installed as pressdigest below) is the one thing that does need to
# stay pressdigest-owned, and this never touches it.
#
# data/, docs/*.pdf, .env, and node_modules are excluded explicitly
# (never ship secrets, the source PDF, or anything derived from one, or a
# multi-hundred-MB node_modules this deploy has no use for); tests/ and
# __pycache__ are excluded as pure dead weight on the target.
# /var/lib/pressdigest/data (the actual runtime data - extracted
# editions) isn't reachable by this rsync at all, by construction: it
# lives on a completely separate path this command never mentions.
rsync -az --delete --rsync-path="sudo rsync" \
  --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.claude' \
  --exclude 'data' \
  --exclude 'docs/*.pdf' \
  --exclude '.env' --exclude '.env.*' \
  --exclude 'tests' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/src' --exclude 'frontend/public' \
  --exclude 'frontend/*.json' --exclude 'frontend/*.ts' --exclude 'frontend/*.html' \
  "$REPO_ROOT/" "$TARGET:$REMOTE_APP_DIR/"

echo "==> Installing dependencies (only if requirements.txt changed) and restarting"
# shellcheck disable=SC2087
ssh "$TARGET" bash -s <<'REMOTE'
set -euo pipefail
APP_DIR=/opt/pressdigest/app
VENV=/opt/pressdigest/venv
HASH_FILE=/opt/pressdigest/.requirements.sha256

NEW_HASH="$(sha256sum "$APP_DIR/deploy/requirements.txt" | awk '{print $1}')"
OLD_HASH="$(cat "$HASH_FILE" 2>/dev/null || true)"

if [ "$NEW_HASH" != "$OLD_HASH" ]; then
  echo "requirements.txt changed - installing"
  sudo -u pressdigest "$VENV/bin/pip" install --no-cache-dir -r "$APP_DIR/deploy/requirements.txt"
  echo "$NEW_HASH" | sudo -u pressdigest tee "$HASH_FILE" > /dev/null
else
  echo "requirements.txt unchanged - skipping install"
fi

sudo systemctl restart pressdigest

echo "waiting for /api/health..."
ok=""
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
    ok=1
    break
  fi
  sleep 2
done
if [ -z "$ok" ]; then
  echo "!!! pressdigest did not become healthy within 60s - deploy FAILED" >&2
  sudo systemctl status pressdigest --no-pager -l || true
  sudo journalctl -u pressdigest -n 50 --no-pager || true
  exit 1
fi
echo "healthy:"
curl -s http://127.0.0.1:8000/api/health && echo
REMOTE

echo
echo "==> Deploy succeeded. Tail logs with: ssh $TARGET 'sudo journalctl -u pressdigest -f'"
