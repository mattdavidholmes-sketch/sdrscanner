#!/bin/bash
# Installs numpy + scipy for the channelizer (many channels at once from one dongle).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:$PATH"
python3 -m pip install --upgrade numpy scipy
python3 -c "import numpy, scipy; print('numpy', numpy.__version__, 'scipy', scipy.__version__, '- channelizer ready')"
echo "Done. Restart the dashboard (it restarts itself within a few seconds)."
touch restart_request.txt
sleep 6
exit 0
