#!/bin/bash
# Makes the scanner reachable from anywhere through a Cloudflare Tunnel on YOUR domain, protected by the app's own password.
# You will: (1) click Authorize in the browser window that opens, (2) type the hostname you want (e.g. scanner.yourdomain.com), (3) choose a password.
cd "$(dirname "$0")/.."; export PATH="/opt/homebrew/bin:$PATH"; APP="$PWD"
echo "== installing cloudflared"; brew list cloudflared >/dev/null 2>&1 || brew install cloudflared
if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
  echo "== a browser window opens: log in to Cloudflare and pick the domain for this scanner, then click Authorize"
  cloudflared tunnel login || { echo "login failed"; read -n 1 -s -r -p "Press any key to close."; exit 1; }
fi
cloudflared tunnel list 2>/dev/null | grep -q " sdr-scanner " || cloudflared tunnel create sdr-scanner
TID=$(cloudflared tunnel list 2>/dev/null | awk '$2=="sdr-scanner"{print $1}' | head -1)
[ -n "$TID" ] || { echo "could not create the tunnel"; read -n 1 -s -r -p "Press any key to close."; exit 1; }
read -r -p "Hostname for the scanner (e.g. scanner.yourdomain.com): " HOST
cloudflared tunnel route dns sdr-scanner "$HOST" 2>&1 | tail -1
mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<Y
tunnel: $TID
credentials-file: $HOME/.cloudflared/$TID.json
ingress:
  - hostname: $HOST
    service: http://localhost:8765
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
  - service: http_status:404
Y
if [ ! -s "$APP/remote_access.txt" ]; then
  read -r -p "Password visitors must enter (no spaces): " PW; echo "$PW" > "$APP/remote_access.txt"
fi
echo "$HOST" > "$APP/sdrtrunk/cloudflare_tunnel.txt"
echo "== done. The dashboard runs the tunnel itself from now on (restart it once: Debug tab -> Restart app). URL: https://$HOST"
read -n 1 -s -r -p "Press any key to close."
