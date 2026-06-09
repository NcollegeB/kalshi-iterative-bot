# Always-On Server Deployment

Use a Debian 12 or Ubuntu 24.04 VM. The bot is installed at `/opt/kalshi-bot`
and runs as the unprivileged `kalshi` user through `systemd`.

## Free Google Cloud VM

Choose only free-tier eligible resources:

- Machine type: `e2-micro`
- Region: `us-west1`, `us-central1`, or `us-east1`
- Boot disk: standard persistent disk, no more than 30 GB
- Operating system: Debian 12
- No GPU, premium image, load balancer, or extra disk

Google requires an active billing account for its Free Tier. Set a budget alert
and verify the estimated monthly cost before creating the VM. Budget alerts do
not automatically cap or stop spending.

From Google Cloud Shell, this creates the intended free-tier-sized VM:

```bash
gcloud compute instances create kalshi-bot \
  --zone=us-west1-b \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-type=pd-standard \
  --boot-disk-size=20GB \
  --no-service-account \
  --no-scopes \
  --network-tier=STANDARD
```

Before continuing, inspect the VM in Billing and confirm that no non-free
resources were added.

Before migrating, rotate any Kalshi API private key that has ever been pasted
into chat, email, or another non-secret channel. Deploy only the replacement
key.

## Install

SSH into the VM and run:

```bash
git clone https://github.com/NcollegeB/kalshi-iterative-bot.git
cd kalshi-iterative-bot
sudo bash deploy/install-server.sh
```

Copy the API private key directly to the server. Never put it in GitHub:

```bash
sudo install -o root -g kalshi -m 0640 /path/to/private-key.pem /etc/kalshi-bot/kalshi-private-key.pem
sudoedit /etc/kalshi-bot/kalshi-bot.env
```

Keep `KALSHI_ALLOW_LIVE=paper_only` while validating:

```bash
sudo systemctl enable --now kalshi-dashboard.service
sudo journalctl -u kalshi-dashboard.service -f
```

To preserve the bot's open-position ledger, realized P&L, and optional learning
history, copy the SQLite database from the Mac while the Mac bot is stopped:

```bash
# Run on the Mac.
pkill -TERM -f '[k]alshi-bot loop' || true
sleep 3
if pgrep -fl '[k]alshi-bot loop'; then
  echo "STOP: a Mac bot loop is still running"
  exit 1
fi
scp data/paper_trades.sqlite3 USER@SERVER_IP:/tmp/paper_trades.sqlite3

# Run on the server.
sudo install -o kalshi -g kalshi -m 0640 /tmp/paper_trades.sqlite3 /opt/kalshi-bot/data/paper_trades.sqlite3
rm /tmp/paper_trades.sqlite3
```

When the server's `live-ready` checks pass, set
`KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK` in
`/etc/kalshi-bot/kalshi-bot.env`, then start the bot:

```bash
sudo -u kalshi bash -c 'set -a; source /etc/kalshi-bot/kalshi-bot.env; KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK exec /opt/kalshi-bot/.venv/bin/kalshi-bot live-ready'
sudo systemctl enable --now kalshi-bot.service
sudo systemctl status kalshi-bot.service
sudo journalctl -u kalshi-bot.service -f
```

Do not restart the Mac live bot after this cutover. Two live loops using the
same account can submit duplicate orders.

The dashboard is deliberately not public. Access it through an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 USER@SERVER_IP
```

Then open `http://127.0.0.1:8765`.

## Stop, Start, And Update

```bash
sudo systemctl stop kalshi-bot.service
sudo systemctl start kalshi-bot.service
sudo systemctl restart kalshi-bot.service
sudo bash /opt/kalshi-bot/deploy/update-server.sh
```
