#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${1:-pi@192.168.68.149}"
REMOTE_ROOT="${2:-/tmp/n0jcg-winlink-source}"
AUTH_OPTION="${3:-}"

if [[ -n "$AUTH_OPTION" && "$AUTH_OPTION" != "--configure-operator-auth" ]]; then
    echo "FAIL: supported optional flag is --configure-operator-auth" >&2
    exit 1
fi

for required in ui branding assets config deploy tools; do
    test -d "$REPO_ROOT/$required" || {
        echo "FAIL: missing source directory: $required" >&2
        exit 1
    }
done

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_ROOT'"
scp -r \
    "$REPO_ROOT/ui" \
    "$REPO_ROOT/branding" \
    "$REPO_ROOT/assets" \
    "$REPO_ROOT/config" \
    "$REPO_ROOT/deploy" \
    "$REPO_ROOT/tools" \
    "$REMOTE_HOST:$REMOTE_ROOT/"

ssh -tt "$REMOTE_HOST" "sudo bash '$REMOTE_ROOT/deploy/install_static_ui.sh' '$AUTH_OPTION'"
