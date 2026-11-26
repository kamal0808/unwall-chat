# You are part of this app

This repo is the one-box chat Kamal talks to (live at https://unwall.kamalkhatwani69.workers.dev). If you are a thread running inside it and Kamal asks to change "you", the box, its page, design, bubble or behaviour, this repo is what you change. Every thread is told the same.

## What is where

- `cf/public/index.html`: the page (stream, input, bubble, right drawer with For you / Working / Earlier, thread panel, settings). Plain HTML and JS, no build step.
- `cf/worker.js`: the Cloudflare Worker and its Durable Object `Box`: messages, threads, settings (models, goals, "yes, always" permissions), the mailbox the relay pulls from. Behind a key (`UNWALL_KEY`).
- `air-relay.py`: the worker on Kamal's MacBook Air. Pulls each message, routes it (answer, existing thread, new thread in a `~/dev` folder), runs `claude -p` in that folder, sends receipts back. Installed as `~/.local/bin/unwall-air-relay.py`, run by launchd `com.kamal.unwall-relay` (`com.kamal.unwall-relay.plist`). Config in `~/.config/unwall/env` (never print it).
- Older pieces from when this ran on the Mac mini: `server.mjs`, `one.mjs`, `index.html`, `home.html`, `mock.html`, `sessions.html`. Not live.

## How to ship a change

- Page or Worker: edit under `cf/`, then deploy from the mini (wrangler is logged in there; don't install it on the Air):
  `scp cf/worker.js mini:dev/unwall-cf/ && scp cf/public/index.html mini:dev/unwall-cf/public/ && ssh mini 'cd ~/dev/unwall-cf && PATH=/opt/homebrew/bin:$PATH npx -y wrangler@4.162.0 deploy'`
  Conversations live in the Durable Object, so a deploy loses nothing; Cloudflare can take a few minutes to switch the Durable Object to new code.
- Relay: `cp air-relay.py ~/.local/bin/unwall-air-relay.py && launchctl kill SIGTERM gui/$(id -u)/com.kamal.unwall-relay`. It stops taking messages, lets running threads (yours included) finish, exits, and launchd starts the new code. Never use `kickstart -k` from inside a thread: it waits for the relay, which waits for you.
- Check `node --check cf/worker.js`, parse the page's script, and `python3 -c "import ast; ast.parse(open('air-relay.py').read())"` before deploying. A broken relay or page breaks the box Kamal would use to ask for the fix.

## Rules

- Work on a branch and merge through a pull request; tasks and Neki go in `topc-social/unwall-tasks`.
- Kamal has said yes, always, to deploying this app and restarting its relay.
- One thread at a time changes this repo; if another thread is mid-change here, say so instead of editing over it.
