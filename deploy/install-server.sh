#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NcollegeB/kalshi-iterative-bot.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/kalshi-bot}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends git python3 python3-venv python3-pip ca-certificates

if ! id kalshi >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin kalshi
fi

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${INSTALL_DIR}/.venv/bin/python" -m pip install -e "${INSTALL_DIR}"

chown -R root:root "${INSTALL_DIR}"
install -d -o kalshi -g kalshi -m 0750 "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
install -d -o root -g kalshi -m 0750 /etc/kalshi-bot

if [[ ! -f /etc/kalshi-bot/kalshi-bot.env ]]; then
  install -o root -g kalshi -m 0640 "${INSTALL_DIR}/deploy/kalshi-bot.env.example" /etc/kalshi-bot/kalshi-bot.env
fi

install -o root -g root -m 0644 "${INSTALL_DIR}/deploy/systemd/kalshi-bot.service" /etc/systemd/system/kalshi-bot.service
install -o root -g root -m 0644 "${INSTALL_DIR}/deploy/systemd/kalshi-dashboard.service" /etc/systemd/system/kalshi-dashboard.service

systemctl daemon-reload
systemctl enable kalshi-dashboard.service

echo "Installed. Add credentials under /etc/kalshi-bot, run live-ready, then explicitly enable and start the live bot."
