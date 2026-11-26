#!/usr/bin/env python3
"""Runs Unwall Chat threads on this Mac (unwall-tasks#14).

Holds one request open to the Unwall server (it answers when there is work, or after 50 s
with nothing), runs the Claude Code engine already installed here in the thread's folder,
and posts the reply back. Idle cost is one sleeping HTTP request; runs get low CPU priority.
"""
import json, os, subprocess, time, urllib.error, urllib.request

SERVER = os.environ.get("UNWALL", "http://100.92.24.17:4321")
HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".local/bin/claude")
# PeopleWiz folders sign in with their own Claude account, as Kamal's shell does
OTHER_ACCOUNT = {"dashoz": "~/.claude-peoplewiz-info", "peoplewiz": "~/.claude-peoplewiz-info"}
# the standalone CLI login expires on its own; a long-lived token (claude setup-token) keeps runs signed in
TOKEN_FILE = os.path.join(HOME, ".config/unwall/env")


def token():
    try:
        return dict(l.strip().split("=", 1) for l in open(TOKEN_FILE) if "=" in l)
    except OSError:
        return {}


def call(path, body=None, timeout=70):
    req = urllib.request.Request(SERVER + path, json.dumps(body).encode() if body is not None else None,
                                 {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def run(job):
    cwd = job["cwd"] if os.path.isdir(job["cwd"]) else os.path.join(HOME, "dev/topc")
    env = dict(os.environ, PATH=f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin", UNWALL_RELAY="1")
    top = os.path.relpath(cwd, os.path.join(HOME, "dev")).split(os.sep)[0]
    if top in OTHER_ACCOUNT:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(OTHER_ACCOUNT[top])
    else:
        env.update(token())
    cmd = ["nice", "-n", "10", CLAUDE, "-p", job["text"], "--output-format", "json",
           "--model", job["model"], "--append-system-prompt", job["append"], "--permission-mode", "auto"]
    if job.get("sessionId"):
        cmd += ["--resume", job["sessionId"]]
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1800)
        r = json.loads(p.stdout or "{}")
        if p.returncode or r.get("is_error"):
            return {"id": job["id"], "error": (r.get("result") or p.stderr or "claude failed")[-400:]}
        return {"id": job["id"], "out": r.get("result", ""), "sessionId": r.get("session_id")}
    except Exception as e:  # a timeout or a bad reply still has to reach the server, or the thread waits forever
        return {"id": job["id"], "error": str(e)[-400:]}


def main():
    while True:
        try:
            status, raw = call("/api/air/next")
            if status == 200:
                call("/api/air/done", run(json.loads(raw)), timeout=30)
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(30)  # server or network down (the mini asleep, Wi-Fi off): back off, don't spin


if __name__ == "__main__":
    main()
