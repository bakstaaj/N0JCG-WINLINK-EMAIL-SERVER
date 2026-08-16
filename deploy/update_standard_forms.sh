#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${N0JCG_APP_ROOT:-/opt/n0jcg-winlink}"
echo "Downloading the official Winlink Standard Forms library..."
python3 "$APP_ROOT/api/winlink_templates.py" "$@"
echo "PASS: Standard Forms installed. Sign out and back in to refresh the Webmail template catalog."
