#!/bin/bash
# Makes the scanner permanent: a login watchdog (launchd, user level) opens Dashboard.command in its one Terminal window at
# login and re-opens it within 3 minutes whenever the dashboard is not answering. macOS privacy rules keep launchd's own shell
# out of the Desktop folder, so the watchdog lives in ~/Library/Application Support/SDR Scanner and only asks Terminal to open the app.
set -e
FOLDER="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Library/Application Support/SDR Scanner"; mkdir -p "$APP" "$HOME/Library/LaunchAgents"
cat > "$APP/watchdog.sh" <<WD
#!/bin/bash
# SDR Scanner watchdog (launchd): if the dashboard does not answer, open Dashboard.command in Terminal (which has Desktop access).
if ! curl -s -m 3 -o /dev/null http://127.0.0.1:8765/meter; then
  sleep 5
  if ! curl -s -m 3 -o /dev/null http://127.0.0.1:8765/meter; then
    echo "\$(date '+%m-%d %H:%M:%S') dashboard not answering - opening Dashboard.command" >> "$APP/watchdog.log"
    /usr/bin/open -a Terminal "$FOLDER/Dashboard.command"
  fi
fi
WD
chmod +x "$APP/watchdog.sh"
cat > "$HOME/Library/LaunchAgents/com.sdrscanner.dashboard.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sdrscanner.dashboard</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$APP/watchdog.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>180</integer>
  <key>StandardOutPath</key><string>$APP/watchdog.log</string>
  <key>StandardErrorPath</key><string>$APP/watchdog.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.sdrscanner.dashboard" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.sdrscanner.dashboard.plist"
sleep 3
echo "watchdog: $(launchctl print "gui/$(id -u)/com.sdrscanner.dashboard" 2>/dev/null | grep -E 'state =' | tr -s ' ')"
echo "Installed. At login (and every 3 min if it is down) the scanner opens in its Terminal window. Dashboard: http://localhost:8765"
exit 0
