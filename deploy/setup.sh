#!/usr/bin/env bash
# Install or update the platform on an Ubuntu/Debian server, behind Caddy.
#
#     bash deploy/setup.sh timetable.example.com
#
# Safe to run again after every `git pull`: it rewrites the same two config
# files and restarts.  Caddy must already be installed, and the domain's A
# record must point at this server, or Caddy cannot obtain a certificate.
set -euo pipefail

DOMAIN="${1:?usage: bash deploy/setup.sh your-domain}"
APP="$(cd "$(dirname "$0")/.." && pwd)"

command -v caddy >/dev/null || { echo "caddy is not installed"; exit 1; }

cd "$APP"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade ortools python-docx

# The app listens on loopback only; Caddy is the one thing facing the
# internet, and its X-Forwarded-Proto is what makes the cookie Secure.
cat > /etc/systemd/system/scheduler.service <<EOF
[Unit]
Description=School timetable scheduler
After=network.target

[Service]
WorkingDirectory=$APP
Environment=PYTHONIOENCODING=utf-8
ExecStart=$APP/.venv/bin/python serve.py --host 127.0.0.1 --port 8000 --data $APP/data
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp && ufw allow 443/tcp
fi

systemctl daemon-reload
systemctl enable --now scheduler
systemctl restart scheduler
systemctl reload caddy || systemctl restart caddy

sleep 2
if systemctl is-active --quiet scheduler; then
    echo "OK - open https://$DOMAIN"
else
    echo "The app did not start. Details:"
    journalctl -u scheduler -n 30 --no-pager
    exit 1
fi
