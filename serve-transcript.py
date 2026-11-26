#!/usr/bin/env python3
"""Forced command for one read-only ssh key: print one Claude Code transcript, nothing else.

On the Mac with the Claude app, ~/.ssh/authorized_keys gets:
  command="/usr/bin/python3 /path/to/serve-transcript.py",restrict ssh-ed25519 AAAA... unwall
Unwall Chat then runs `ssh <mac> <session-id>` and reads the .jsonl from stdout.
"""
import glob, os, re, sys

sid = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", sid):
    sys.exit("expected a session id")
found = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
if not found:
    sys.exit("no such session")
with open(found[0], "rb") as f:
    sys.stdout.buffer.write(f.read())
