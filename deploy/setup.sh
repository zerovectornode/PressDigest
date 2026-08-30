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
# python3-venv/pip: the app's own virtualenv. nginx: reverse proxy +
# static asset serving + basic auth. apache2-utils: htpasswd, to create
# the basic-auth credential file. rsync: what deploy.sh uses to ship code
# from the laptop.
#
# build-essential/libjpeg62-turbo/zlib1g are NOT required by anything in
# this codebase - verified directly (see design/DESIGN.md "Deployment: GCP
# e2-micro VM"): pdfplumber's page.to_image() uses pypdfium2's own bundled
# PDFium binary, no poppler/ImageMagick/ghostscript subprocess call
# anywhere, and the vision-image render that used to call it was removed
# as dead code (nothing ever read it back). Installed anyway as cheap
# insurance against a missing prebuilt wheel for this exact base image -
# there's no local machine to test this setup script against before it
# runs for real.
apt-get update
apt-get install -y --no-install-recommends \
    python3-venv python3-pip nginx apache2-utils rsync \
    build-essential libjpeg62-turbo zlib1g

echo "==> swapfile (1GB RAM is tight - pdfplumber extraction is the heaviest step)"
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "created 2GB swapfile"
else
  echo "swapfile already exists, skipping"
fi

echo "==> service user"
if ! id -u pressdigest &>/dev/null; then
  useradd --system --home-dir /opt/pressdigest --shell /usr/sbin/nologin pressdigest
fi

echo "==> directories"
# Everything under /opt/pressdigest stays root-owned EXCEPT data/ -
# deploy.sh's rsync and the venv's pip installs both run as root (over
# sudo), which would conflict with pressdigest ownership on the code/venv
# anyway (setuptools editable installs write an egg-info dir into the
# source tree). Root-owned code and venv are still fully usable by the
# pressdigest user: reading and executing don't need ownership, only
# write access does, and the only directory this service ever writes to
# is data/ (see pressdigest.service's ReadWritePaths) - that's the one
# that needs pressdigest ownership, and it's excluded from deploy.sh's
# rsync scope so this chown never gets clobbered by a later deploy.
mkdir -p /opt/pressdigest/app/data
mkdir -p /opt/pressdigest/venv
chown -R pressdigest:pressdigest /opt/pressdigest/app/data

if [ ! -f /opt/pressdigest/venv/bin/python ]; then
  python3 -m venv /opt/pressdigest/venv
fi

echo "==> environment file"
mkdir -p /etc/pressdigest
if [ ! -f /etc/pressdigest/pressdigest.env ]; then
  cp "$(dirname "$0")/pressdigest.env.example" /etc/pressdigest/pressdigest.env
  echo "wrote /etc/pressdigest/pressdigest.env - edit it now and set GEMINI_API_KEY"
fi
chown root:root /etc/pressdigest/pressdigest.env
chmod 600 /etc/pressdigest/pressdigest.env

echo "==> systemd unit"
cp "$(dirname "$0")/pressdigest.service" /etc/systemd/system/pressdigest.service
systemctl daemon-reload
systemctl enable pressdigest

echo "==> nginx site"
cp "$(dirname "$0")/nginx-pressdigest.conf" /etc/nginx/sites-available/pressdigest
ln -sf /etc/nginx/sites-available/pressdigest /etc/nginx/sites-enabled/pressdigest
rm -f /etc/nginx/sites-enabled/default
nginx -t

echo
echo "==> Setup script done. Remaining manual steps:"
echo "  1. Edit /etc/pressdigest/pressdigest.env and set GEMINI_API_KEY"
echo "  2. Create the basic-auth user:"
echo "       htpasswd -c /etc/nginx/pressdigest.htpasswd <username>"
echo "     (drop -c on any *additional* user, it truncates the file)"
echo "  3. From your laptop, run deploy/deploy.sh <user>@<this-vm> to ship the app"
echo "  4. systemctl restart nginx"
echo "  5. systemctl status pressdigest --no-pager -l"
