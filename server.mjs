import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {homedir} from 'node:os';
import {query, listSessions, getSessionMessages} from '@anthropic-ai/claude-agent-sdk';

const PORT = Number(process.env.PORT || 4321);
// bind to one address (the Tailscale IP on the mini): this server runs an agent on the machine, so never expose it on the LAN
const HOST = process.env.HOST || '127.0.0.1';
const CWD = process.env.CWD || homedir();
const STATIC = {'/': 'index.html', '/unwall.html': 'unwall.html'};

// one Claude turn = the tools it used plus everything it said
function turns(messages) {
  const out = [];
  for (const m of messages) {
    if (m.parent_tool_use_id) continue;
    const c = m.message?.content;
    if (m.type === 'user') {
      const text = typeof c === 'string' ? c : (c || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
      if (text && !text.startsWith('<')) out.push({role: 'user', text});
      continue;
    }
    if (m.type !== 'assistant' || !Array.isArray(c)) continue;
    let t = out.at(-1);
    if (t?.role !== 'claude') out.push(t = {role: 'claude', text: '', tools: []});
    for (const b of c) {
      if (b.type === 'text') t.text += (t.text ? '\n\n' : '') + b.text;
      if (b.type === 'tool_use') t.tools.push(b.name);
    }
  }
  return out;
}

async function send(req, res) {
  let body = '';
  for await (const chunk of req) body += chunk;
  const {text, sessionId} = JSON.parse(body || '{}');
  if (typeof text !== 'string' || !text.trim()) return json(res, 400, {error: 'empty message'});
  res.writeHead(200, {'content-type': 'application/x-ndjson', 'cache-control': 'no-store'});
  const line = o => res.write(JSON.stringify(o) + '\n');
  const ac = new AbortController();
  res.on('close', () => ac.abort());
  try {
    for await (const m of query({prompt: text, options: {
      cwd: CWD, resume: sessionId || undefined, includePartialMessages: true, abortController: ac,
      settingSources: ['user', 'project', 'local'], permissionMode: 'auto',
      // shortcut: anything auto mode would ask about is denied, until the page can show approve/deny buttons
      canUseTool: async () => ({behavior: 'deny', message: 'Unwall Chat cannot ask for approval yet, so this was not allowed.'}),
    }})) {
      if (m.parent_tool_use_id) continue;
      if (m.type === 'system' && m.subtype === 'init') line({type: 'session', id: m.session_id});
      else if (m.type === 'stream_event' && m.event.type === 'content_block_delta' && m.event.delta.type === 'text_delta') line({type: 'text', delta: m.event.delta.text});
      else if (m.type === 'stream_event' && m.event.type === 'content_block_stop') line({type: 'break'});
      else if (m.type === 'assistant') { for (const b of m.message.content) if (b.type === 'tool_use') line({type: 'tool', name: b.name}) }
      else if (m.type === 'result' && m.is_error) line({type: 'error', message: m.result || m.subtype});
    }
    line({type: 'done'});
  } catch (e) {
    if (!ac.signal.aborted) line({type: 'error', message: String(e.message || e)});
  }
  res.end();
}

const json = (res, code, o) => { res.writeHead(code, {'content-type': 'application/json'}); res.end(JSON.stringify(o)) };

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  try {
    if (STATIC[url.pathname]) {
      res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      return res.end(await readFile(new URL(STATIC[url.pathname], import.meta.url)));
    }
    if (url.pathname === '/api/chats') {
      const list = await listSessions({dir: CWD, limit: 40});
      return json(res, 200, list.map(s => ({id: s.sessionId, title: s.customTitle || s.summary || s.firstPrompt || 'Untitled', at: s.lastModified})));
    }
    const m = url.pathname.match(/^\/api\/chats\/([\w-]+)$/);
    if (m) return json(res, 200, turns(await getSessionMessages(m[1], {dir: CWD})));
    if (url.pathname === '/api/send' && req.method === 'POST') return send(req, res);
    json(res, 404, {error: 'not found'});
  } catch (e) {
    json(res, 500, {error: String(e.message || e)});
  }
}).listen(PORT, HOST, () => console.log(`Unwall Chat on http://${HOST}:${PORT} (agent cwd ${CWD})`));
