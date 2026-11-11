#!/bin/bash
# Publishes nets.json (the ham net schedule) to GitHub the way every other change goes out: the local main branch keeps the full
# history and is never pushed; the public main gets one new commit on the "publish" branch, and only after the personal-data scan
# finds nothing. The scan pattern is kept out of the repository, in _helpers/NOTE_publish_scan.txt (one extended regex on one line).
# Run it from the dashboard by writing "publish_nets.command" into open_request.txt; the result is in diag/publish_nets.txt.
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:$PATH"
mkdir -p diag
{
echo "=== $(date '+%Y-%m-%d %H:%M:%S') publish nets.json"
python3 ham_nets.py > /dev/null || { echo "nets.json does not load - nothing published"; exit 1; }
PAT=$(head -n 1 _helpers/NOTE_publish_scan.txt 2>/dev/null)
[ -n "$PAT" ] || { echo "no scan pattern (_helpers/NOTE_publish_scan.txt) - nothing published"; exit 1; }
if git diff --quiet HEAD -- nets.json; then echo "nets.json is the same as the last commit - nothing to publish"; exit 0; fi
MSG="Net schedule check $(date '+%Y-%m-%d')"
git add nets.json && git commit -q -m "$MSG" || { echo "commit failed"; exit 1; }
N=$(git grep -I -i -E "$PAT" HEAD -- . ':!licences_gta.csv' | wc -l | tr -d ' ')
if [ "$N" != "0" ]; then echo "personal-data scan: $N hit(s) - committed locally, NOT pushed"; exit 1; fi
NEW=$(git commit-tree 'HEAD^{tree}' -p publish -m "$MSG") && git branch -f publish "$NEW" && git push origin publish:main 2>&1 | tail -1
echo "scan hits 0; local $(git log --oneline -1 | cut -c1-50); public main $(git rev-parse --short origin/main)"
echo "=== done $(date '+%H:%M:%S')"
} > diag/publish_nets.txt 2>&1
