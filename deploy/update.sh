#!/usr/bin/env bash
# Deploy whatever main now holds.  Run every two minutes by
# scheduler-update.timer, which deploy/setup.sh installs.
#
# A new commit goes live only if the test suite passes on this server; a
# commit that fails is remembered and not retried until main moves again.
# The restart waits for a running solve to finish, because solves live in
# the app's memory and a restart would lose one without a word.
#
# Everything is inside main(): the merge below may rewrite this very file,
# and bash reads a script as it runs, so the whole body must be parsed first.
set -euo pipefail

main() {
    local app health failed old new
    app="$(cd "$(dirname "$0")/.." && pwd)"
    health="${HEALTH:?HEALTH is set by scheduler-update.service}"
    failed="$app/.deploy-failed"
    cd "$app"

    git fetch --quiet origin main
    old="$(git rev-parse HEAD)"
    new="$(git rev-parse origin/main)"
    [ "$old" != "$new" ] || return 0
    if [ "$(cat "$failed" 2>/dev/null)" = "$new" ]; then
        return 0
    fi

    echo "Deploying ${new:0:7} (was ${old:0:7})"
    git merge --ff-only --quiet origin/main

    if ! PYTHONIOENCODING=utf-8 .venv/bin/python -m unittest discover \
            -s tests -t . > "$app/.deploy-tests.log" 2>&1; then
        git reset --hard --quiet "$old"
        echo "$new" > "$failed"
        echo "Tests failed on ${new:0:7}; staying on ${old:0:7}."
        tail -n 30 "$app/.deploy-tests.log"
        return 1
    fi

    # Wait out a running solve, for up to three hours.
    local waited=0
    while curl -fsS --max-time 5 "$health" 2>/dev/null | grep -q '"busy": true'; do
        [ "$waited" -lt 10800 ] || { echo "Solve still running; restarting anyway."; break; }
        sleep 30
        waited=$((waited + 30))
    done

    systemctl restart scheduler
    rm -f "$failed"
    echo "Live on ${new:0:7}."
}

main "$@"
exit
