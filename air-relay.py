#!/usr/bin/env python3
"""Unwall's worker on Kamal's Mac (unwall-tasks#14, #15).

Holds one request open to the Unwall box on Cloudflare (answered when there is a message, or after
50 s with nothing). For each message it decides where it goes, then answers it or runs it in a
thread, using the Claude Code engine already installed here, in Kamal's real folders and tools.
Idle cost is one sleeping HTTP request; Claude runs get low CPU priority.
"""
import base64, glob, json, os, re, subprocess, threading, time, urllib.error, urllib.request

HOME = os.path.expanduser("~")
IMG_DIR = os.path.join(HOME, ".cache/unwall-images")
CLAUDE = os.path.join(HOME, ".local/bin/claude")
DEV = os.path.join(HOME, "dev")
MODELS = {"Haiku": "haiku", "Sonnet": "sonnet", "Opus": "opus"}
# PeopleWiz folders sign in with their own Claude account, as Kamal's shell does
OTHER_ACCOUNT = {"dashoz": "~/.claude-peoplewiz-info", "peoplewiz": "~/.claude-peoplewiz-info"}
SESSIONS = os.path.join(HOME, "Library/Application Support/Claude/claude-code-sessions")
RECEIPT = """Kamal reads only the last part of your reply, so end it in one of two ways:
- "RECEIPT: " followed by the result itself in at most 3 short lines. If he asked a question, that is the answer with its facts, numbers or list, not a description of what you did. If you changed something, say what changed and where he can check it.
- "ASK: " followed by one question, only when you truly cannot go on without him: you need his yes for something risky or outward (sending, posting, paying, deleting), or the answer exists nowhere you can look. Never ask what you can find out yourself from files, tools, the TOPC brain or past sessions; decide, say what you assumed, and finish the work."""


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
    if job.get("quote") and any(t["id"] == job["quote"] for t in job["threads"]):
        return {"go": "thread", "id": job["quote"]}
    line = lambda t: f'- id {t["id"]}: "{t["name"]}" in ~/dev/{t["folder"]}, {"closed" if t.get("closed") else t.get("state")}: {(t.get("ask") or t.get("receipt") or t.get("last") or "")[:160]}'
    folders = ", ".join(sorted(d for d in os.listdir(DEV) if os.path.isdir(os.path.join(DEV, d)) and not d.startswith((".", "_"))))
    prompt = ("You route Kamal's messages. Send a message to a thread only when it is about that thread's own subject; use the recent "
              "conversation to tell what \"it\" or \"that\" means. Questions about the overall picture (what is running, how many sessions "
              "or threads, what is waiting, what was decided, where something was left) are answers, even right after a thread's work.\n\n"
              f"Recent conversation:\n{recent(job)}\n\nThreads:\n" + ("\n".join(map(line, job["threads"][-15:])) or "(none)") +
              f"\n\nProject folders in ~/dev: {folders}\n\nKamal's new message:\n\"\"\"{job['text']}\"\"\"\n\n"
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
              f"Past conversations whose titles match:\n{notes or '(none)'}\n\nKamal's question: {job['text']}")
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


def run_thread(job, t, text):
    with LOCKS.setdefault(t["id"], threading.Lock()):  # one message at a time per thread
        t.update(state="working", last=text[:80]); save_thread(t)
        try:
            # a reopened desktop session is forked, so the app's own copy stays as it was
            fork = t.pop("fork", False)
            out, sid = claude(text, os.path.join(DEV, t["folder"]), MODELS[job["settings"]["threads"]], t.get("sessionId"), RECEIPT, fork=fork, tid=t["id"])
            t["sessionId"] = sid if fork else (t.get("sessionId") or sid)
            asked = (m.group(1).strip() if (m := re.search(r"^ASK:\s*([\s\S]+)$", out, re.M)) else None)
            receipt = (m.group(1) if (m := re.search(r"^RECEIPT:\s*([\s\S]+)$", out, re.M)) else out.strip().split("\n\n")[-1]).strip()[:500]
            t.update(receipt=receipt, ask=asked[:400] if asked else None, state="needs" if asked else "done")
        except Exception as e:
            out = ""
            t.update(state="failed", ask=None, receipt=f"failed: {str(e)[:120]}")
        save_thread(t, [{"role": "user", "text": text}, {"role": "claude", "text": out}] if out else [{"role": "user", "text": text}])
        if t.get("ask"):
            event(kind="ask", thread=t["id"], text=t["ask"])
        else:
            event(kind="receipt", thread=t["id"], text=t["receipt"], failed=t["state"] == "failed")


def handle(job):
    print("job", job.get("re"), "revive" if job.get("revive") else "message", flush=True)
    try:
        paths = []
        for i, d in enumerate(job.get("imgs") or []):
            head, _, b64 = d.partition(",")
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(head[5:].split(";")[0], "jpg")
            os.makedirs(IMG_DIR, exist_ok=True)
            paths.append(os.path.join(IMG_DIR, f"{job['re']}-{i}.{ext}"))
            open(paths[-1], "wb").write(base64.b64decode(b64))
        if paths:
            job["text"] += "\n\n[Kamal attached images; look at them with the Read tool: " + ", ".join(paths) + "]"
        if job.get("revive"):
            p = job["revive"]
            t = {"id": f"{int(time.time() * 1000):x}", "name": p["title"][:60], "folder": p["cwd"] or os.path.join(DEV, "topc"),
                 "state": "queued", "sessionId": p["cli"], "fork": True}
            save_thread(t)
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
        t.update(closed=False, ask=None)
        save_thread(t)
        event(kind="ack", thread=t["id"], fresh=fresh, reopened=reopened, re=job["re"])
        run_thread(job, t, brief(job) + job["text"] if fresh else job["text"])
    except Exception as e:  # never leave a message without a reply in the stream
        try:
            event(kind="claude", text=f"Something broke on your Mac while handling this: {str(e)[:200]}", re=job.get("re"))
        except Exception:
            pass


def main():
    while True:
        try:
            status, job = call("/api/relay/next")
            if status == 200 and job:
                call("/api/relay/ack", {"re": job["re"]}, timeout=30)  # or the box hands it out again
                threading.Thread(target=handle, args=(job,), daemon=True).start()
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(30)  # box or network down (Wi-Fi off, Cloudflare unreachable): back off, don't spin


if __name__ == "__main__":
    main()
