import json, glob, os, re, datetime, sys

PROJ = os.path.expanduser('~/.claude/projects')
APP = os.path.expanduser('~/Library/Application Support/Claude/claude-code-sessions')

SECRET = [
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)',
    r'\b(?:sk-|sk_|rk_|pk_live_|rzp_live_|rzp_test_|gh[pousr]_|github_pat_|glpat-|xox[abposr]-|sbp_|sb_secret_|nfp_|npm_|hf_|AKIA|ASIA|AIza|ya29\.|whsec_|re_)[A-Za-z0-9_\-./]{8,}',
    r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*',
    r'(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{12,}',
    r'(?i)(?<=://)[^/\s:@]+:[^/\s@]+(?=@)',
    r'(?i)(?:password|passwd|pwd|passcode|secret|token|api[ _-]?key|otp|pin)\b["\'`*]*\s*(?:is|[:=])\s*["\'`*]*(?!\[)[^\s"\'`*,;]{4,}',
    # anything 22+ that mixes cases and digits and is not a path or an id
    r'(?<![A-Za-z0-9_+=-])(?![A-Za-z0-9_+=-]*-[A-Za-z]{2,}-[A-Za-z]{2,}-)(?=[A-Za-z0-9_+=-]*[a-z])(?=[A-Za-z0-9_+=-]*[A-Z])(?=[A-Za-z0-9_+=-]*\d)[A-Za-z0-9_+=-]{22,}(?![A-Za-z0-9_+=-])',
    r'(?<![A-Za-z0-9])[a-fA-F0-9]{32,}(?![A-Za-z0-9])',
]

def redact(s):
    for p in SECRET:
        s = re.sub(p, '[redacted]', s, flags=re.S)
    return s

def clean(s):
    s = re.sub(r'<(system-reminder|ci-monitor-event|task-notification|side-sessions-event|local-command-stdout|local-command-caveat)>.*?</\1>', '', s, flags=re.S)
    m = re.search(r'<command-name>(.*?)</command-name>', s, re.S)
    if m:
        a = re.search(r'<command-args>(.*?)</command-args>', s, re.S)
        s = (m[1] + ' ' + (a[1] if a else '')).strip()
    s = re.sub(r'</?pasted_content[^>]*>', '', s)
    return redact(s.strip())

def entries(paths):
    for p in paths:
        for line in open(p):
            try: yield json.loads(line)
            except ValueError: pass

def visible(o):
    """(who, text) for a message the person typed or Claude showed, else None."""
    if o.get('isSidechain') or o.get('isMeta') or o.get('isCompactSummary') or o.get('isApiErrorMessage'):
        return None
    t = o.get('type')
    if t == 'attachment':
        a = o.get('attachment', {})
        if a.get('type') != 'queued_command' or (a.get('origin') or {}).get('kind') != 'human': return None
        who, c = 'k', a.get('prompt')
    elif t == 'user':
        if (o.get('origin') or {}).get('kind') != 'human': return None
        who, c = 'k', o['message']['content']
    elif t == 'assistant':
        if o['message'].get('model') == '<synthetic>': return None
        who, c = 'c', o['message']['content']
    else:
        return None
    if isinstance(c, list):
        parts = [b.get('text', '') if b.get('type') == 'text' else '[image]' if b.get('type') == 'image' and who == 'k' else '' for b in c]
        c = '\n'.join(x for x in parts if x)
    c = clean(c or '')
    return (who, c) if c else None

def load():
    meta = {}
    for f in glob.glob(APP + '/*/*/local_*.json'):
        m = json.load(open(f)); meta[m['sessionId']] = m
    files = {os.path.basename(f)[:-6]: f for f in glob.glob(PROJ + '/*/*.jsonl')}
    return meta, files

def session(m, files):
    ids = (m.get('priorCliSessionIds') or []) + [m.get('cliSessionId')]
    paths = [files[i] for i in ids if i in files]
    if not paths: return None
    msgs, spawns, seen = [], [], set()
    for o in entries(paths):
        if o.get('uuid') in seen: continue
        seen.add(o.get('uuid'))
        v = visible(o)
        if v: msgs.append({'w': v[0], 't': v[1][:6000] + ('\n[cut]' if len(v[1]) > 6000 else ''), 'at': o.get('timestamp', '')})
        c = (o.get('message') or {}).get('content')
        if o.get('type') == 'user' and isinstance(c, list):
            for b in c:
                if b.get('type') == 'tool_result': spawns.append((len(msgs) - 1, json.dumps(b.get('content'))))
    return {'id': m['sessionId'], 'title': redact(m.get('title') or 'untitled'), 'msgs': msgs, 'spawns': spawns}

def fork_point(parent, child_meta):
    """index of the parent message the child started from, and whether it was guessed from time."""
    sf = child_meta.get('spawnedFrom') or {}
    for needle in (child_meta['sessionId'], sf.get('taskId')):
        for i, txt in parent['spawns']:
            if needle and needle in txt and ('Started session' in txt or needle == sf.get('taskId') or 'session_id' in txt):
                return max(i, 0), False
    born = datetime.datetime.fromtimestamp(child_meta['createdAt'] / 1000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    before = [i for i, x in enumerate(parent['msgs']) if x['at'][:19] <= born]
    return (before[-1] if before else 0), True
