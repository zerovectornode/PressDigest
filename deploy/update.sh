#!/usr/bin/env bash
# Run ON THE VM (Cloud Shell / browser SSH), as root, for every update:
#   sudo bash /opt/pressdigest/app/deploy/update.sh
# Idempotent - safe to re-run, including with nothing new to pull.
#
# There is no laptop and no rsync in this design (see
# .github/workflows/deploy.yml): GitHub Actions builds the frontend and
# publishes everything the VM needs to the `deploy` branch (a single,
# force-pushed commit each time - see that workflow's comments for why).
# This script's only job is to move /opt/pressdigest/app to that latest
# commit and restart.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo bash update.sh)" >&2
  exit 1
fi

APP_DIR=/opt/pressdigest/app
VENV=/opt/pressdigest/venv
HASH_FILE=/opt/pressdigest/.requirements.sha256

echo "==> Fetching deploy branch"
# reset --hard, not pull/merge: the deploy branch is CI-generated and
# force-pushed (one commit, always - see the workflow) specifically so
# there is never a merge to reason about here, only "move to whatever
# commit is there now." Runs as the pressdigest user throughout, same as
# setup.sh's initial clone - nothing here needs root beyond this script's
# own systemctl/pip-as-another-user calls, and keeping the whole
# /opt/pressdigest tree owned by one low-privilege identity is simpler
# than the root-vs-service-user split the earlier rsync-based design
# needed (see git history - that split existed only because rsync ran
# from a laptop identity that wasn't pressdigest; there's no laptop in
# this design at all now).
sudo -u pressdigest git -C "$APP_DIR" fetch origin deploy
BEFORE_REV="$(sudo -u pressdigest git -C "$APP_DIR" rev-parse HEAD)"
sudo -u pressdigest git -C "$APP_DIR" reset --hard origin/deploy
AFTER_REV="$(sudo -u pressdigest git -C "$APP_DIR" rev-parse HEAD)"
echo "    $BEFORE_REV -> $AFTER_REV"

echo "==> Installing dependencies (only if requirements.txt changed)"
NEW_HASH="$(sha256sum "$APP_DIR/deploy/requirements.txt" | awk '{print $1}')"
OLD_HASH="$(cat "$HASH_FILE" 2>/dev/null || true)"
if [ "$NEW_HASH" != "$OLD_HASH" ]; then
  echo "    requirements.txt changed - installing"
  sudo -u pressdigest "$VENV/bin/pip" install --no-cache-dir -r "$APP_DIR/deploy/requirements.txt"
  echo "$NEW_HASH" | sudo -u pressdigest tee "$HASH_FILE" > /dev/null
else
  echo "    unchanged - skipping install"
fi

echo "==> Restarting"
systemctl restart pressdigest

echo "==> Waiting for /api/health"
ok=""
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
    ok=1
    break
  fi
  sleep 2
done
if [ -z "$ok" ]; then
  echo "!!! pressdigest did not become healthy within 60s - update FAILED" >&2
  systemctl status pressdigest --no-pager -l || true
  journalctl -u pressdigest -n 50 --no-pager || true
  exit 1
fi
echo "==> Healthy:"
curl -s http://127.0.0.1:8000/api/health && echo
