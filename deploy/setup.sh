#!/usr/bin/env bash
# Install or update the platform on an Ubuntu/Debian server, behind Caddy.
#
#     bash deploy/setup.sh timetable.example.com
#
# Safe to run again after every `git pull`.  The domain's A record must
# point at this server, or Caddy cannot obtain a certificate.
#
# Two layouts, chosen by what already holds port 443:
#
# * nothing  - Caddy is installed on the host (it must be installed first)
#              and the app listens on loopback behind it.
# * a Caddy container (another site on the same server) - only one program
#              can own 443, so that Caddy gets a site block for the domain,
#              and the app listens on the container network's gateway: the
#              host's address as the container sees it, and not reachable
#              from outside.  The container's config is backed up first and
#              restored if Caddy refuses the new one.
set -euo pipefail

DOMAIN="${1:?usage: bash deploy/setup.sh your-domain}"
APP="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000

fail() { echo "$*"; exit 1; }

# -------------------------------------------------------------- which layout

CONTAINER=""
if command -v docker >/dev/null; then
    CONTAINER="$(docker ps --filter publish=443 --format '{{.Names}}' | head -n1)"
fi

if [ -n "$CONTAINER" ]; then
    image="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"
    case "$image" in
        caddy*) ;;
        *) fail "Port 443 belongs to container $CONTAINER ($image), not Caddy." ;;
    esac

    # The config path the container runs with, then the host file behind it.
    args="$(docker inspect -f '{{join .Args " "}}' "$CONTAINER")"
    CONF_IN="$(echo "$args" | sed -n 's/.*--config[= ]\([^ ]*\).*/\1/p')"
    CONF_IN="${CONF_IN:-/etc/caddy/Caddyfile}"
    CONF_HOST=""
    while IFS='|' read -r src dst; do
        [ -n "$dst" ] || continue
        case "$CONF_IN" in
            "$dst") CONF_HOST="$src" ;;
            "$dst"/*) CONF_HOST="$src${CONF_IN#"$dst"}" ;;
        esac
    done < <(docker inspect -f \
        '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' \
        "$CONTAINER")
    [ -f "$CONF_HOST" ] || fail "Cannot find $CONTAINER's $CONF_IN on the host."

    HOST="$(docker inspect -f \
        '{{range .NetworkSettings.Networks}}{{.Gateway}} {{end}}' \
        "$CONTAINER" | awk '{print $1}')"
    [ -n "$HOST" ] || fail "Cannot find $CONTAINER's network gateway."
    echo "Using Caddy in container $CONTAINER ($CONF_HOST), app on $HOST:$PORT"
else
    command -v caddy >/dev/null || fail "caddy is not installed"
    taken="$(ss -tlnpH '( sport = :80 or sport = :443 )' \
             | grep -v '"caddy"' || true)"
    [ -z "$taken" ] || fail "Ports 80/443 are already used by:
$taken"
    HOST=127.0.0.1
fi

# ---------------------------------------------------------------- the app

cd "$APP"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade ortools python-docx

cat > /etc/systemd/system/scheduler.service <<EOF
[Unit]
Description=School timetable scheduler
After=network.target docker.service

[Service]
WorkingDirectory=$APP
Environment=PYTHONIOENCODING=utf-8
ExecStart=$APP/.venv/bin/python serve.py --host $HOST --port $PORT --data $APP/data
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now scheduler
systemctl restart scheduler

# -------------------------------------------------------------- the proxy

if [ -n "$CONTAINER" ]; then
    # A host Caddy from an earlier run would fight the container for 443.
    systemctl disable --now caddy 2>/dev/null || true

    if grep -q "^$DOMAIN[ {]" "$CONF_HOST"; then
        echo "$CONF_HOST already has a block for $DOMAIN - left as is."
    else
        backup="$CONF_HOST.bak.$(date +%Y%m%d%H%M%S)"
        cp -p "$CONF_HOST" "$backup"
        # Appended, not rewritten: a single-file bind mount follows the
        # inode, and `sed -i` would replace it behind the container's back.
        printf '\n%s {\n\treverse_proxy %s:%s\n}\n' \
            "$DOMAIN" "$HOST" "$PORT" >> "$CONF_HOST"
        if ! docker exec "$CONTAINER" caddy reload --config "$CONF_IN"; then
            cat "$backup" > "$CONF_HOST"
            fail "Caddy refused the new config; restored $backup."
        fi
        echo "Added $DOMAIN to $CONF_HOST (backup: $backup)"
    fi
else
    cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    reverse_proxy $HOST:$PORT
}
EOF
    if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
        ufw allow 80/tcp && ufw allow 443/tcp
    fi
    systemctl enable caddy
    systemctl reload caddy || systemctl restart caddy
fi

# ------------------------------------------------------------------ verify

sleep 3
units="scheduler"
[ -n "$CONTAINER" ] || units="scheduler caddy"
for unit in $units; do
    if ! systemctl is-active --quiet "$unit"; then
        echo "$unit did not start. Details:"
        journalctl -u "$unit" -n 30 --no-pager
        exit 1
    fi
done
if [ -n "$CONTAINER" ] && ! docker exec "$CONTAINER" \
        wget -q -O /dev/null "http://$HOST:$PORT/" 2>/dev/null; then
    echo "Warning: $CONTAINER cannot reach the app at $HOST:$PORT."
    echo "A firewall on the host may be dropping traffic from Docker."
fi
echo "OK - open https://$DOMAIN (the first load may take ~30s for the certificate)"
