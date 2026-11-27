#!/usr/bin/env python3
"""Unwall's worker on Kamal's Mac (unwall-tasks#14, #15).

Holds one request open to the Unwall box on Cloudflare (answered when there is a message, or after
50 s with nothing). For each message it decides where it goes, then answers it or runs it in a
thread, using the Claude Code engine already installed here, in Kamal's real folders and tools.
Idle cost is one sleeping HTTP request; Claude runs get low CPU priority.
"""
import base64, glob, json, os, re, signal, subprocess, threading, time, urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
IMG_DIR = os.path.join(HOME, ".cache/unwall-images")
CLAUDE = os.path.join(HOME, ".local/bin/claude")
DEV = os.path.join(HOME, "dev")
MODELS = {"Haiku": "haiku", "Sonnet": "sonnet", "Opus": "opus"}
# PeopleWiz folders sign in with their own Claude account, as Kamal's shell does
OTHER_ACCOUNT = {"dashoz": "~/.claude-peoplewiz-info", "peoplewiz": "~/.claude-peoplewiz-info"}
SESSIONS = os.path.join(HOME, "Library/Application Support/Claude/claude-code-sessions")
RECEIPT = """Kamal reads only the end of your reply. End it like this:
RECEIPT: the result itself in at most 3 short lines. If he asked a question, that is the answer with its facts, numbers or list, not a description of what you did. If you changed something, say what changed and where he can check it.
Then, only when the next move is a decision that is his (his yes before sending, posting, paying or deleting; a choice between approaches or drafts), add:
NEXT: one short question
CHOICES: {"mode": "next", "options": [{"label": "1-3 words", "note": "one line", "preview": "full draft text, only when choosing between drafts", "rec": true}]}
mode "next" = 1-2 buttons, your pick first; "one" = pick one of 2-4; "many" = tick several. Put "rec": true on your pick. He can always type his own answer, so never add an "Other" option.
Never ask what you can find out yourself from files, tools, the TOPC brain or past sessions: look it up, decide, say what you assumed, and finish the work."""


RAMU_RECALL = ("ssh mini 'cd ~/dev/ramu-kaka && python3 -c \"import ramu_memory,sys; print(ramu_memory.recall(sys.argv[1], "
               "\\\"/Users/kamal/dev/ramu-kaka/memory\\\", budget_tokens=900, touch=False))\" \"<what to look up>\"'")


def side(t):
    """Personal threads live in Ramu's folder; everything else is work."""
    return "personal" if "ramu-kaka" in (t or {}).get("folder", "") else "work"


def goals_rule(t):
    if side(t) == "personal":
        own, other = f"Ramu's brain (run: {RAMU_RECALL})", "work"
    else:
        own, other = "the TOPC brain (Basic Memory projects topc and kamal-career)", "personal"
    return (f"\n\nThis is a {side(t)} thread. Before deciding, look up the goals of Kamal's that matter for this task in {own}. "
            f"Never read the {other} side's brain yourself: personal and work goals are walled off. If his {other} goals matter here, "
            f"end your reply with one line \"CROSS: <what to look up> | <why it matters here>\" and stop; he decides each time.")


def guidance(job):
    """What every thread works by: Kamal's goals, what he has already said yes to, and how hard to try."""
    st = job["settings"]
    goals = (st.get("goals") or "").strip()
    perms = "\n".join(f"- {p}" for p in st.get("permissions") or [])
    return ("\n\n" + (f"Kamal's goals, to decide by when he isn't there to ask:\n{goals}\n\n" if goals else "") +
            (f"Kamal has already said yes, always, to these; do them without asking again:\n{perms}\n\n" if perms else "") +
            "Don't give up when one way fails: try other routes (other tools, the CLI, the mini's browser, past sessions, the brain) "
            "until the result he wants is reached, carefully, and only stop for a yes on something outward or risky.\n"
            "You are a thread inside Kamal's one-box chat app (Unwall). Its own code is ~/dev/unwall-chat; when Kamal asks to change "
            "you, the box, its page, design or behaviour, read ~/dev/unwall-chat/AGENTS.md and change that code, then deploy it as it says.\n"
            "Use the skill made for the work before doing it: public copy and posts go through topc-marketing and the CMO flow, "
            "co-founders and distribution through founderbot, buying through shop, TOPC work through topc-task-first; read a site's "
            "rules before posting there.")


def config():
    """UNWALL (the box's address), UNWALL_KEY and the long-lived Claude token, from ~/.config/unwall/env."""
    try:
        return dict(l.strip().split("=", 1) for l in open(os.path.join(HOME, ".config/unwall/env")) if "=" in l)
    except OSError:
        return {}


CFG = config()
SERVER = CFG.get("UNWALL", "http://100.92.24.17:4321")


def call(path, body=None, timeout=70):
    req = urllib.request.Request(SERVER + path, json.dumps(body).encode() if body is not None else None,
                                 {"content-type": "application/json", "authorization": f"Bearer {CFG.get('UNWALL_KEY', '')}",
                                  "user-agent": "unwall-relay/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def event(**e):
    call("/api/relay/event", e, timeout=30)


def save_thread(t, log=None):
    call("/api/relay/thread", {"thread": t, "log": log or []}, timeout=30)


def claude(prompt, cwd, model, resume=None, append=None, tools=True, fork=False, tid=None):
    cwd = cwd if os.path.isdir(cwd) else os.path.join(DEV, "topc")
    env = dict(os.environ, PATH=f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin", UNWALL_RELAY="1")
    top = os.path.relpath(cwd, DEV).split(os.sep)[0]
    if top in OTHER_ACCOUNT:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(OTHER_ACCOUNT[top])
    elif CFG.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = CFG["CLAUDE_CODE_OAUTH_TOKEN"]
    cmd = ["nice", "-n", "10", CLAUDE, "-p", prompt, "--output-format", "json", "--model", model, "--permission-mode", "auto"]
    if append:
        cmd += ["--append-system-prompt", append]
    if resume:
        cmd += ["--resume", resume] + (["--fork-session"] if fork else [])
    if not tools:
        cmd += ["--tools", ""]
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1800)
    r = json.loads(p.stdout or "{}")
    if p.returncode or r.get("is_error"):
        raise RuntimeError((r.get("result") or p.stderr or "claude failed")[-300:])
    u = r.get("usage") or {}
    tokens = sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    try:
        call("/api/relay/usage", {"thread": tid, "tokens": tokens, "cost": r.get("total_cost_usd") or 0}, timeout=30)
    except Exception:
        pass  # a missed counter must never fail the answer
    return r.get("result", ""), r.get("session_id")


def recent(job):
    names = {t["id"]: t["name"] for t in job["threads"]}
    lines = []
    for s in job["recent"]:
        n, text = names.get(s.get("thread"), "?"), (s.get("text") or "")[:250]
        lines.append({"you": f"Kamal: {text}", "ack": f'  (sent to thread "{n}")', "receipt": f'  thread "{n}" replied: {text}',
                      "ask": f'  thread "{n}" asked Kamal: {text}', "claude": f"  answered: {text}"}.get(s["kind"], ""))
    return "\n".join(l for l in lines if l) or "(nothing yet)"


def desktop_sessions():
    out = []
    for path in glob.glob(os.path.join(SESSIONS, "*", "*", "local_*.json")):
        try:
            s = json.load(open(path))
        except (OSError, ValueError):
            continue
        out.append({"title": s.get("title") or "Untitled", "cwd": s.get("cwd") or "", "cli": s.get("cliSessionId"),
                    "archived": bool(s.get("isArchived")), "active": s.get("lastActivityAt") or s.get("createdAt") or 0})
    return out


def last_words(s):
    """How a past desktop session ended: its last 4 turns, read from its transcript here."""
    files = glob.glob(os.path.join(HOME, ".claude/projects", "*", f"{s['cli']}.jsonl")) if s.get("cli") else []
    if not files:
        return ""
    turns = []
    for line in open(files[0], errors="ignore"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        msg = r.get("message") or {}
        if r.get("type") not in ("user", "assistant") or r.get("isSidechain"):
            continue
        c = msg.get("content")
        text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
        text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
        if text and not text.startswith("<"):
            turns.append(f"{'Kamal' if r['type'] == 'user' else 'Claude'}: {text[:1200]}")
    return "\n".join(turns[-4:])


STOP = set("what where when which with that this from have about status still done going should would could there their them they then".split())


def past_sessions(text, all_sessions):
    flat = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    words = {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOP}
    scored = [(sum(w in flat(s["title"]) for w in words), s) for s in all_sessions]
    return [s for n, s in sorted((x for x in scored if x[0]), key=lambda x: (-x[0], -x[1]["active"]))[:6]]


def overview(job, all_sessions):
    now = time.time() * 1000
    live = sorted((s for s in all_sessions if not s["archived"] and s["active"] > now - 864e5), key=lambda s: -s["active"])
    threads = "\n".join(f'- "{t["name"]}" (~/dev/{t["folder"]}): {"closed" if t.get("closed") else t.get("state")}'
                        f'{", asking: " + t["ask"][:120] if t.get("ask") else ""}{", last: " + t["receipt"][:120] if t.get("receipt") else ""}'
                        for t in job["threads"]) or "(none)"
    return (f"Threads in this box:\n{threads}\n\nClaude desktop sessions on this Mac: {len(all_sessions)} total, "
            f"{sum(s['archived'] for s in all_sessions)} archived, {len(live)} not archived and active in the last 24 hours, "
            f"{sum(s['active'] > now - 36e5 for s in live)} in the last hour. Active in the last 24 hours, newest first:\n"
            + "\n".join(f'- "{s["title"]}" (~/{os.path.relpath(s["cwd"], HOME) if s["cwd"] else "?"}, '
                        f'{time.strftime("%d %b %H:%M", time.localtime(s["active"] / 1000))})' for s in live[:25]))


def route(job):
    """Where a message goes: an answer, an existing thread, or a new thread in a folder."""
    pinned = next((t for t in job["threads"] if t["id"] == job.get("quote")), None)
    # an answer to a thread's own words (Answer, Yes/No, a quote) always goes back to it; plain Reply mode is only a hint
    if pinned and job["text"].lstrip().startswith(">"):
        return {"go": "thread", "id": pinned["id"]}
    line = lambda t: f'- id {t["id"]}: "{t["name"]}" in ~/dev/{t["folder"]}, {"closed" if t.get("closed") else t.get("state")}: {(t.get("ask") or t.get("receipt") or t.get("last") or "")[:160]}'
    folders = ", ".join(sorted(d for d in os.listdir(DEV) if os.path.isdir(os.path.join(DEV, d)) and not d.startswith((".", "_"))))
    prompt = ("You route Kamal's messages. Send a message to a thread only when it is about that thread's own subject; use the recent "
              "conversation to tell what \"it\" or \"that\" means. Questions about the overall picture (what is running, how many sessions "
              "or threads, what is waiting, what was decided, where something was left) are answers, even right after a thread's work.\n\n"
              "A new problem, product or service that a thread's name and work don't cover is a new topic, even right after that thread "
              "spoke. When unsure, start a new thread.\n\n" +
              (f'Kamal had Reply open on thread {pinned["id"]} ("{pinned["name"]}"): prefer it only if the message is about that thread\'s subject.\n\n' if pinned else "") +
              f"Recent conversation:\n{recent(job)}\n\nThreads:\n" + ("\n".join(map(line, job["threads"][-15:])) or "(none)") +
              f"\n\nProject folders in ~/dev: {folders}\nThis box itself (its page, design, bubble, behaviour) is the folder unwall-chat.\n\nKamal's new message:\n\"\"\"{job['text']}\"\"\"\n\n"
              'Reply with JSON only, one of:\n{"go":"thread","id":"<thread id>"} when it continues a thread\'s work (a closed one gets reopened)\n'
              '{"go":"new","name":"<2-4 word topic>","folder":"<one folder from the list, or topc if none fits>"} for work on a new topic\n'
              '{"go":"answer"} for a question about the overall picture or about what was decided or where something stands, which needs no work in a project')
    try:
        out, _ = claude(prompt, os.path.join(DEV, "topc"), MODELS[job["settings"]["main"]], tools=False)
        r = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
    except Exception:
        r = {}
    if r.get("go") == "thread" and any(t["id"] == r.get("id") for t in job["threads"]):
        return r
    if r.get("go") == "answer":
        return r
    return {"go": "new", "name": r.get("name") or " ".join(job["text"].split()[:4]), "folder": r.get("folder") or "topc"}


def answer(job):
    sessions = desktop_sessions()
    past = past_sessions(job["text"], sessions)
    notes = "\n\n".join(f'Session "{s["title"]}" ({time.strftime("%a %d %b", time.localtime(s["active"] / 1000))}, ~/{os.path.relpath(s["cwd"], HOME) if s["cwd"] else "?"}), '
                        + (f"how it ended:\n{w}" if (w := last_words(s)) else "transcript not found") for s in past[:3])
    prompt = ("Answer Kamal in plain words, at most 5 short lines. Give the answer itself first, then where it came from.\n"
              "Sources, in order: the whole picture below (threads here and his Claude desktop sessions), the past conversations below, "
              "then the TOPC brain (Basic Memory, project \"topc\"). If none has it, say so in one line.\n\n"
              f"The whole picture right now:\n{overview(job, sessions)}\n\nRecent conversation here:\n{recent(job)}\n\n"
              f"Past conversations whose titles match:\n{notes or '(none)'}\n\nKamal's question: {job['text']}" + guidance(job))
    out, _ = claude(prompt, os.path.join(DEV, "topc"), MODELS[job["settings"]["main"]])
    event(kind="claude", text=out.strip(), re=job["re"],
          sessions=[{"title": s["title"], "cli": s["cli"], "cwd": s["cwd"], "active": s["active"]} for s in past[:3]])


def brief(job):
    """What a new thread knows before Kamal's message: the conversation so far and earlier sessions on the topic."""
    past = past_sessions(job["text"], desktop_sessions())[:2]
    notes = "\n\n".join(f'"{s["title"]}" ({time.strftime("%d %b", time.localtime(s["active"] / 1000))}):\n{w}' for s in past if (w := last_words(s)))
    return ("You are a thread in Unwall, Kamal's one box. Before asking him anything, look it up: the TOPC brain (Basic Memory, "
            "project topc), the earlier sessions below, files and tools here.\n\n"
            f"The conversation in the box so far:\n{recent(job)}\n\n" + (f"Earlier sessions on this:\n{notes}\n\n" if notes else "") +
            "Kamal's message:\n")


LOCKS = {}


def next_step(out):
    """The question a reply ends with (NEXT, or the older ASK) and its CHOICES, cleaned for the page's cards."""
    q = (m.group(1).strip() if (m := re.search(r"^(?:NEXT|ASK):\s*(.+?)\s*(?=^CHOICES:|\Z)", out, re.M | re.S)) else None)
    choices = None
    if q and (m := re.search(r"^CHOICES:\s*", out, re.M)):
        try:
            c, _ = json.JSONDecoder().raw_decode(out[m.end():])
            opts = [{k: str(o[k])[:1500 if k == "preview" else 120] for k in ("label", "note", "preview") if o.get(k)} | ({"rec": True} if o.get("rec") else {})
                    for o in c.get("options", []) if isinstance(o, dict) and o.get("label")][:6]
            if opts:
                choices = {"mode": c.get("mode") if c.get("mode") in ("next", "one", "many") else ("one" if len(opts) > 2 else "next"), "options": opts}
        except (ValueError, AttributeError):
            pass
    return q, choices


LOOKUP = re.compile(r"\b(want me to|should i|shall i|do you want me to|can i|may i)\b[^?]*\b(look|check|find|search|research|compare|pull|get|dig|see)\b", re.I)


def run_thread(job, t, text):
    with LOCKS.setdefault(t["id"], threading.Lock()):  # one message at a time per thread
        t.update(state="working", last=text[:80]); save_thread(t)
        try:
            # a reopened desktop session is forked, so the app's own copy stays as it was
            fork = t.pop("fork", False)
            out, sid = claude(text, os.path.join(DEV, t["folder"]), MODELS[job["settings"]["threads"]], t.get("sessionId"), RECEIPT + guidance(job) + goals_rule(t), fork=fork, tid=t["id"])
            t["sessionId"] = sid if fork else (t.get("sessionId") or sid)
            asked, choices = next_step(out)
            if asked and LOOKUP.search(asked):  # an offer to look something up isn't a question for Kamal: send it back to do it
                out, _ = claude(f"Don't ask Kamal that; do it now: {asked}\nThen give him the answer itself.", os.path.join(DEV, t["folder"]),
                                MODELS[job["settings"]["threads"]], t["sessionId"], RECEIPT + guidance(job) + goals_rule(t), tid=t["id"])
                asked, choices = next_step(out)
            if (m := re.search(r"^CROSS:\s*(.+?)\s*\|\s*(.+)$", out, re.M)):
                other = "work" if side(t) == "personal" else "personal"
                t["cross"] = {"query": m.group(1).strip()[:200], "why": m.group(2).strip()[:300], "to": other}
                asked = f"Wants to look at your {other} goals about \"{t['cross']['query']}\": {t['cross']['why']}"
            body = re.split(r"^(?:NEXT|ASK|CHOICES):", out, maxsplit=1, flags=re.M)[0]
            receipt = (m.group(1) if (m := re.search(r"^RECEIPT:\s*([\s\S]+)$", body, re.M)) else body.strip().split("\n\n")[-1]).strip()[:500]
            t.update(receipt=receipt, ask=asked[:400] if asked else None, choices=choices if asked else None, state="needs" if asked else "done")
        except Exception as e:
            out = ""
            t.update(state="failed", ask=None, receipt=f"failed: {str(e)[:120]}")
        save_thread(t, [{"role": "user", "text": text}, {"role": "claude", "text": out}] if out else [{"role": "user", "text": text}])
        # the result always shows; a question for Kamal follows it as its own card
        if t.get("receipt") and not (t.get("cross") and t.get("ask")):
            event(kind="receipt", thread=t["id"], text=t["receipt"], failed=t["state"] == "failed")
        if t.get("ask"):
            event(kind="cross" if t.get("cross") else "ask", thread=t["id"], text=t["ask"], **(t.get("choices") or {}))


def history(cli):
    """A desktop session's recent turns, so a reopened thread shows where it was."""
    files = glob.glob(os.path.join(HOME, ".claude/projects", "*", f"{cli}.jsonl"))
    turns = []
    for line in open(files[0], errors="ignore") if files else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in ("user", "assistant") or r.get("isSidechain"):
            continue
        c = (r.get("message") or {}).get("content")
        text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
        text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
        if text and not text.startswith("<"):
            turns.append({"role": "user" if r["type"] == "user" else "claude", "text": text[:3000]})
    return turns[-30:]


def look_up(to, query):
    """The one door between the walled-off brains, opened only on Kamal's Allow once."""
    if to == "personal":
        p = subprocess.run(["ssh", "mini", "cd ~/dev/ramu-kaka && python3 -c 'import ramu_memory,sys; print(ramu_memory.recall(sys.argv[1], "
                            "\"/Users/kamal/dev/ramu-kaka/memory\", budget_tokens=900, touch=False))' " + json.dumps(query + " goals")],
                           capture_output=True, text=True, timeout=120)
        return p.stdout.strip()
    out, _ = claude(f"Search the TOPC brain (Basic Memory, projects topc and kamal-career) for Kamal's work goals about: {query}. "
                    "Reply with only the matching goals and decisions, one per line, with their dates.", os.path.join(DEV, "topc"), "haiku")
    return out.strip()


def crossing(job, t):
    """Kamal's Allow once or No on a thread's CROSS request, turned into the thread's next message."""
    c, t["cross"] = t.get("cross") or {}, None  # explicit None: the box merges thread fields
    if job.get("cross") != "allow" or not c:
        return f"Kamal said no to looking at his {c.get('to', 'other')} goals. Go on without them."
    found = look_up(c["to"], c["query"]) or "(nothing found)"
    return (f"Kamal allowed this once. His {c['to']} goals about \"{c['query']}\":\n{found}\n\n"
            "Use them only for this question. Don't save them, copy them into files, or quote them in anything others will see.")


def remember_always(job):
    """A reply like "yes, always" to a thread's question becomes a standing permission."""
    t = next((x for x in job["threads"] if x["id"] == job.get("quote")), None)
    reply = re.sub(r"^(>.*\n?)+", "", job["text"]).strip().lower()
    if t and t.get("ask") and re.search(r"\balways\b", reply) and re.match(r"(yes|yeah|yep|ok|okay|sure|go)\b", reply):
        call("/api/relay/permission", {"text": t["ask"]}, timeout=30)


def handle(job):
    print("job", job.get("re"), "revive" if job.get("revive") else "message", flush=True)
    try:
        paths = []
        for i, d in enumerate(job.get("imgs") or []):
            head, _, b64 = d.partition(",")
            nm = re.search(r";name=([^;]*)", head)
            mime = head[5:].split(";")[0]
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime, "jpg")
            fname = re.sub(r"[^\w.-]", "_", urllib.parse.unquote(nm.group(1)))[:60] if nm else f"image.{ext}"
            os.makedirs(IMG_DIR, exist_ok=True)
            paths.append(os.path.join(IMG_DIR, f"{job['re']}-{i}-{fname}"))
            open(paths[-1], "wb").write(base64.b64decode(b64))
        if paths:
            job["text"] += "\n\n[Kamal attached files or images; look at them with the Read tool: " + ", ".join(paths) + "]"
        remember_always(job)
        if job.get("revive"):
            p = job["revive"]
            t = {"id": f"{int(time.time() * 1000):x}", "name": p["title"][:60], "folder": p["cwd"] or os.path.join(DEV, "topc"),
                 "state": "queued", "sessionId": p["cli"], "fork": True}
            save_thread(t, history(p["cli"]))
            event(kind="ack", thread=t["id"], fresh=True, re=job["re"])
            return run_thread(job, t, "Kamal reopened this conversation from Unwall. In at most 3 lines: where it was left, and what is next.")
        r = route(job)
        if r["go"] == "answer":
            return answer(job)
        t = next((dict(x) for x in job["threads"] if x["id"] == r.get("id")), None)
        fresh = t is None
        if fresh:
            name = r["name"]
            t = {"id": f"{int(time.time() * 1000):x}", "name": name[:1].upper() + name[1:], "folder": r["folder"], "state": "queued"}
        reopened = bool(t.get("closed"))
        if job.get("cross") and t.get("cross"):
            job["text"] = crossing(job, t)
        t.update(closed=False, ask=None)
        save_thread(t)
        event(kind="ack", thread=t["id"], fresh=fresh, reopened=reopened, re=job["re"])
        run_thread(job, t, brief(job) + job["text"] if fresh else job["text"])
    except Exception as e:  # never leave a message without a reply in the stream
        try:
            event(kind="claude", text=f"Something broke on your Mac while handling this: {str(e)[:200]}", re=job.get("re"))
        except Exception:
            pass


HALT = threading.Event()


def main():
    # an update or restart (SIGTERM from launchd) stops taking work and lets running threads finish first
    signal.signal(signal.SIGTERM, lambda *_: HALT.set())
    workers = []
    while not HALT.is_set():
        try:
            status, job = call("/api/relay/next")
            if status == 200 and job and not HALT.is_set():
                call("/api/relay/ack", {"re": job["re"]}, timeout=30)  # or the box hands it out again
                w = threading.Thread(target=handle, args=(job,))
                w.start(); workers.append(w)
            workers = [w for w in workers if w.is_alive()]
        except (urllib.error.URLError, OSError, ValueError):
            HALT.wait(30)  # box or network down (Wi-Fi off, Cloudflare unreachable): back off, don't spin
    for w in workers:
        w.join()


if __name__ == "__main__":
    main()
