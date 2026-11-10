#!/bin/bash
cd "$(dirname "$0")"; export PATH="/opt/homebrew/bin:$PATH"
{ date; echo "--- port 8765:"; lsof -nP -iTCP:8765 -sTCP:LISTEN 2>/dev/null; echo "--- procs:"; ps -axo pid=,command= | grep -E "dashboard2|Dashboard.command" | grep -v grep | cut -c1-120; echo "--- import test:"; python3 -c "import dashboard2" 2>&1 | tail -15; } > diag/crash.txt 2>&1
exit 0
