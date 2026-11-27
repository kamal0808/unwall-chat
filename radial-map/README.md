# Radial conversation map

Claude in the centre, one branch per Claude desktop session, one dot per message; a sub-session forks from the message that started it. A mock for `topc-social/unwall-tasks#30`.

```
python3 check.py
python3 build.py <session-id> <session-id> ...
node check.cjs ~/conversation-map/conversation-map.html
```

Session ids are the part after `local_` in `~/Library/Application Support/Claude/claude-code-sessions/*/*/`. The first characters are enough. Pick which sessions go in: nothing is filtered for you.

The page is one local HTML file with your message text in it. Keys are redacted by pattern, which can miss things, so don't share it. `--dummy` writes the same shape with placeholder text.

Not shown: answers given on question cards, subagents, and the briefs one session sends another.
