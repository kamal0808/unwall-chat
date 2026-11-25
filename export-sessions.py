#!/usr/bin/env python3
"""Print every Claude desktop (Code tab) session as JSON: id, title, folder, archived, dates.

Run on the Mac that has the desktop app; pipe it to wherever Unwall Chat runs, e.g.
  python3 export-sessions.py | ssh mini 'cat > ~/.unwall/sessions.json'
Only these fields leave the machine: the session files also hold permission settings and MCP config.
"""
import glob, json, os, sys

ROOT = os.path.expanduser(os.environ.get(
    'CLAUDE_SESSIONS_DIR', '~/Library/Application Support/Claude/claude-code-sessions'))

out = []
for path in glob.glob(os.path.join(ROOT, '*', '*', 'local_*.json')):
    try:
        with open(path) as f:
            s = json.load(f)
    except (OSError, ValueError):
        continue
    out.append({
        'id': s.get('sessionId'),
        'title': s.get('title') or 'Untitled',
        'cwd': s.get('cwd') or '',
        'archived': bool(s.get('isArchived')),
        # a session started from another sits under it in the app's sidebar, until moved to top level
        'parent': None if s.get('lineageDetached') else (s.get('spawnedFrom') or {}).get('sessionId'),
        'created': s.get('createdAt'),
        'active': s.get('lastActivityAt') or s.get('createdAt'),
    })
out.sort(key=lambda s: s['active'] or 0, reverse=True)
json.dump(out, sys.stdout)
