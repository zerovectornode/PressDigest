#!/usr/bin/env bash
# First-time setup for a fresh Debian 12 e2-micro VM. Meant to run once;
# safe to re-run (every step checks before acting) if you need to repair
# something rather than rebuild the VM from scratch.
#
# Run on the VM as root: sudo bash setup.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo bash setup.sh)" >&2
  exit 1
fi

echo "==> apt packages"
# Deliberately generous, not minimal - there is no local copy of this VM
# to test against, so a missing shared library surfaces as a runtime
# crash (or worse, a crash on the ONE code path - a specific PDF's font
# subset, an unusual image filter - that happens not to be exercised
# until real use) rather than a loud install-time failure. Covers:
#   - python3-venv, python3-pip, python3-dev: the app's own virtualenv,
#     and headers in case anything needs to build instead of using a
#     prebuilt wheel.
#   - build-essential: compiler fallback for the same reason.
#   - libjpeg62-turbo, libpng16-16, zlib1g, libtiff6, libfreetype6,
#     liblcms2-2, libopenjp2-7, libwebp7: Pillow's full image-format
#     matrix. Modern Pillow wheels bundle all of these statically, so
#     none of this is expected to actually be needed - installed anyway
#     as insurance, per the same "cannot test locally" reasoning.
#   - libxml2, libxslt1.1: some PDFs route text/font metadata through
#     XML-based structures (XMP metadata, some font programs); pdfminer.six
#     doesn't hard-require these, but they're cheap insurance too.
#   - rsync: what deploy.sh uses to ship code from the laptop.
#   - curl, gnupg, debian-keyring, debian-archive-keyring,
#     apt-transport-https: needed to add Caddy's official apt repo below
#     (per caddyserver.com/docs/install#debian-ubuntu-raspbian).
apt-get update
apt-get install -y --no-install-recommends \
    python3-venv python3-pip python3-dev build-essential \
    libjpeg62-turbo libpng16-16 zlib1g libtiff6 libfreetype6 liblcms2-2 libopenjp2-7 libwebp7 \
    libxml2 libxslt1.1 \
    rsync curl gnupg debian-keyring debian-archive-keyring apt-transport-https

echo "==> swapfile (1GB RAM is tight - pip installing pdfplumber's tree, and"
echo "    extraction itself, both assume 2GB swap is active)"
if ! swapon --show=NAME --noheadings | grep -q .; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap activated"
else
  echo "swap already active, skipping"
fi
if ! swapon --show=NAME --noheadings | grep -q .; then
  echo "!!! WARNING: swap is NOT active after attempting to enable it." >&2
  echo "!!! pip installs and/or extraction may fail with OOM. Investigate before continuing." >&2
fi

echo "==> Caddy (official repo, not Debian's bundled/older package)"
if ! command -v caddy &>/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
else
  echo "caddy already installed, skipping"
fi

echo "==> service user"
if ! id -u pressdigest &>/dev/null; then
  useradd --system --home-dir /opt/pressdigest --shell /usr/sbin/nologin pressdigest
fi

echo "==> directories"
# App code + venv and the actual data live on separate trees on purpose:
# deploy.sh's rsync only ever touches /opt/pressdigest/app, so
# /var/lib/pressdigest/data - extracted editions, the only copy of what a
# Gemini call returned - can never be wiped by a routine redeploy.
#
# Ownership: pressdigest owns everything initially (this chown). After
# the first deploy.sh run, /opt/pressdigest/app becomes root-owned
# instead - deploy.sh's rsync runs as root over SSH (the connecting
# account isn't the pressdigest user, so it needs root to write into a
# directory it doesn't own) - which is fine, since pressdigest.service
# only ever READS app/ (importing over PYTHONPATH, never writing back
# into the source tree the way an editable pip install would - see
# requirements.txt). /opt/pressdigest/venv and /var/lib/pressdigest stay
# pressdigest-owned indefinitely: deploy.sh's pip step runs as that user
# explicitly, and this rsync never touches /var/lib/pressdigest at all.
mkdir -p /opt/pressdigest/app
mkdir -p /var/lib/pressdigest/data
chown -R pressdigest:pressdigest /opt/pressdigest /var/lib/pressdigest

if [ ! -f /opt/pressdigest/venv/bin/python ]; then
  sudo -u pressdigest python3 -m venv /opt/pressdigest/venv
fi

echo "==> app secrets"
mkdir -p /etc/pressdigest
if [ ! -f /etc/pressdigest/env ]; then
  cp "$(dirname "$0")/env.example" /etc/pressdigest/env
  echo "wrote /etc/pressdigest/env - edit it now and set GEMINI_API_KEY"
fi
chown root:root /etc/pressdigest/env
chmod 600 /etc/pressdigest/env

echo "==> caddy environment (site address, basic auth)"
if [ ! -f /etc/pressdigest/caddy.env ]; then
  cp "$(dirname "$0")/caddy.env.example" /etc/pressdigest/caddy.env
  echo "wrote /etc/pressdigest/caddy.env - edit it now (BASIC_AUTH_USER/HASH at minimum)"
fi
chown root:caddy /etc/pressdigest/caddy.env 2>/dev/null || chown root:root /etc/pressdigest/caddy.env
chmod 640 /etc/pressdigest/caddy.env

echo "==> systemd: pressdigest.service"
cp "$(dirname "$0")/pressdigest.service" /etc/systemd/system/pressdigest.service

echo "==> systemd: pruning timer"
cp "$(dirname "$0")/pressdigest-prune.service" /etc/systemd/system/pressdigest-prune.service
cp "$(dirname "$0")/pressdigest-prune.timer" /etc/systemd/system/pressdigest-prune.timer

echo "==> systemd: caddy drop-in (reads /etc/pressdigest/caddy.env)"
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/override.conf <<'EOF'
[Service]
EnvironmentFile=/etc/pressdigest/caddy.env
EOF

echo "==> caddy config"
cp "$(dirname "$0")/Caddyfile" /etc/caddy/Caddyfile

systemctl daemon-reload
systemctl enable pressdigest
systemctl enable pressdigest-prune.timer
systemctl start pressdigest-prune.timer
systemctl enable caddy

echo
echo "==> Setup script done. Remaining manual steps:"
echo "  1. Edit /etc/pressdigest/env and set GEMINI_API_KEY"
echo "  2. Generate a basic-auth hash: caddy hash-password --plaintext '<password>'"
echo "     Put the username and that hash into /etc/pressdigest/caddy.env"
echo "  3. From your laptop, run deploy/deploy.sh <user>@<this-vm> to ship the app"
echo "  4. systemctl restart caddy"
echo "  5. systemctl status pressdigest caddy --no-pager -l"
