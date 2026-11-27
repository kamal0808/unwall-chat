import sys
from extract import *
# session ids (or their first characters), roots and sub-sessions; the output holds private text, so it stays outside the repo
PICK = [a for a in sys.argv[1:] if not a.startswith('--')]
assert PICK, 'usage: build.py <session-id>... [--dummy]'
OUT = os.path.expanduser('~/conversation-map/')
os.makedirs(OUT, mode=0o700, exist_ok=True)
meta, files = load()
chosen = {sid: session(m, files) for p in PICK for sid, m in meta.items() if sid.startswith('local_' + p)}
assert len(chosen) == len(PICK) and all(chosen.values()), 'an id matched no session, or more than one'
out = []
for sid, s in chosen.items():
    parent = (meta[sid].get('spawnedFrom') or {}).get('sessionId')
    c = {'id': sid, 'title': s['title'], 'msgs': s['msgs'], 'parent': None, 'forkAt': 0, 'guessed': False}
    if parent in chosen:
        c['parent'] = parent
        c['forkAt'], c['guessed'] = fork_point(chosen[parent], meta[sid])
    out.append(c)
if '--dummy' in sys.argv:  # same shape, no real words: safe to render on another machine
    for n, c in enumerate(out):
        c['title'] = f'Sample conversation number {n + 1}'
        for m in c['msgs']: m['t'] = 'sample text ' * max(1, len(m['t']) // 12)
data = json.dumps(out).replace('<', '\\u003c')
name = 'dummy.html' if '--dummy' in sys.argv else 'conversation-map.html'
open(OUT + name, 'w').write(open(os.path.join(os.path.dirname(__file__), 'template.html')).read().replace('__DATA__', data))
os.chmod(OUT + name, 0o600)
for c in out:
    print(c['id'][6:14], 'k', sum(m['w'] == 'k' for m in c['msgs']), 'claude', sum(m['w'] == 'c' for m in c['msgs']),
          'parent', (c['parent'] or '-')[6:14], 'fork', c['forkAt'], 'GUESS' if c['guessed'] else '')
print(OUT + name, os.path.getsize(OUT + name) // 1024, 'KB; redactions', data.count('[redacted]'))
