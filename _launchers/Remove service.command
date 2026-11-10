#!/bin/bash
# Removes the login watchdog (nothing else changes; Dashboard.command still works by double-click).
launchctl bootout "gui/$(id -u)/com.sdrscanner.dashboard" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/com.sdrscanner.dashboard.plist"
echo "login watchdog removed"
exit 0
