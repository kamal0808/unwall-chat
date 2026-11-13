# Unwall Chat

A Claude Code chat app where long answers arrive unwalled. It looks like a normal chat (chats in a sidebar, a reply box), but any Claude reply over 150 words opens in [Unwall](https://github.com/tinker20/unwall): Overview, Cards and Story instead of a wall of text.

Paused experiment. Task and Neki: [topc-social/unwall-tasks#8](https://github.com/topc-social/unwall-tasks/issues/8).

## Run it

Needs Node 20+ and a Claude login token (`claude setup-token`).

```bash
npm install
echo "CLAUDE_CODE_OAUTH_TOKEN=..." > .env
CWD=~/some/project npm start
```

Open http://127.0.0.1:4321. `HOST` sets the address to listen on; keep it private (localhost or a Tailscale IP), since the server runs Claude Code on that machine.

## How it works

- `server.mjs` runs Claude Code through the Claude Agent SDK in `CWD`: lists and resumes its sessions, streams replies to the page. Auto mode; anything it would ask about is denied for now.
- `index.html` is the chat. Long replies load `unwall.html` in a frame through Unwall's `#add=` link.
- `unwall.html` is Unwall's page, copied unchanged from tinker20/unwall (MIT, `LICENSE-unwall`).

## Left to do

- Approve/deny buttons for tool permissions.
- Run as a background service.
