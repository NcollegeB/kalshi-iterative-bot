#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this updater with sudo." >&2
  exit 1
fi

restart_services() {
  systemctl start kalshi-dashboard.service kalshi-bot.service
}

trap restart_services EXIT

systemctl stop kalshi-bot.service kalshi-dashboard.service
git -C /opt/kalshi-bot pull --ff-only
/opt/kalshi-bot/.venv/bin/python -m pip install -e /opt/kalshi-bot
restart_services
trap - EXIT
systemctl --no-pager --full status kalshi-bot.service
