#!/bin/bash
# Run on the Mac with the Claude app: send the session list and the conversations touched in
# the last DAYS days to the machine running Unwall Chat, so they can be opened there.
# shortcut: recent days only, to spare the mini's disk; widen DAYS or sync on demand when older ones matter
set -e
HOST=${HOST:-mini}; DAYS=${DAYS:-3}
cd "$(dirname "$0")"
ssh "$HOST" 'mkdir -p ~/.unwall'
python3 export-sessions.py | ssh "$HOST" 'cat > ~/.unwall/sessions.json'
cd ~/.claude/projects
find . -maxdepth 2 -name '*.jsonl' -mtime -"$DAYS" | rsync -q --files-from=- . "$HOST:.claude/projects/"
echo "synced list + $(find . -maxdepth 2 -name '*.jsonl' -mtime -"$DAYS" | wc -l | tr -d ' ') conversations from the last $DAYS days"
