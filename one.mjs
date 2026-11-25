// One box: every message goes to a router that answers it, sends it to an open thread, or starts a
// new one. Threads are background Claude Code sessions; the page polls /api/one for what changed.
import {readFile, writeFile, mkdir, readdir} from 'node:fs/promises';
import {homedir} from 'node:os';
import {query, getSessionMessages} from '@anthropic-ai/claude-agent-sdk';

const DIR = `${homedir()}/.unwall`;
const STATE = `${DIR}/one.json`;
const MODELS = {Haiku: 'haiku', Sonnet: 'sonnet', Opus: 'opus'};
const BRAIN = {'basic-memory': {type: 'http', url: 'http://127.0.0.1:8765/mcp'}};
const RECEIPT = `When you finish, end your last message with one line of the form "RECEIPT: <what happened, at most 12 words>". If you had to stop because something needs Kamal's yes, say so in that line.`;

let state = {settings: {main: 'Sonnet', threads: 'Sonnet'}, threads: {}, stream: [], seq: 0};
const ready = readFile(STATE, 'utf8').then(s => { state = {...state, ...JSON.parse(s)} }).catch(() => {});
const save = async () => { await mkdir(DIR, {recursive: true}); await writeFile(STATE, JSON.stringify(state)) };
const push = item => { state.stream.push({id: ++state.seq, at: Date.now(), ...item}); return save() };
const busy = {}; // thread id -> promise of its current run, so one thread handles one message at a time

async function ask(prompt, opts) {
  let out = '', sessionId = null;
  for await (const m of query({prompt, options: {settingSources: ['user', 'project', 'local'], ...opts}})) {
    if (m.type === 'system' && m.subtype === 'init') sessionId = m.session_id;
    if (m.type === 'result') { if (m.is_error) throw new Error(m.result || m.subtype); out = m.result || '' }
  }
  return {out, sessionId};
}

async function folders() {
  const dev = `${homedir()}/dev`;
  return (await readdir(dev, {withFileTypes: true})).filter(d => d.isDirectory() && !d.name.startsWith('_') && !d.name.startsWith('.')).map(d => d.name);
}

// the router only decides where a message goes; it never does the work
async function route(text) {
  const open = Object.values(state.threads).filter(t => !t.closed)
    .map(t => `- id ${t.id}: "${t.name}" in ~/dev/${t.folder}, ${t.state}: ${t.receipt || t.last || ''}`).join('\n') || '(none)';
  const prompt = `You route Kamal's messages. Open threads:\n${open}\n\nProject folders in ~/dev: ${(await folders()).join(', ')}\n\nKamal's message:\n"""${text}"""\n\n` +
    `Reply with JSON only, one of:\n{"go":"thread","id":"<open thread id>"} when it continues an open thread's work\n` +
    `{"go":"new","name":"<2-4 word topic>","folder":"<one folder from the list, or topc if none fits>"} for work on a new topic\n` +
    `{"go":"answer"} for a quick question about what was decided or how things stand, which needs no work done`;
  const {out} = await ask(prompt, {model: MODELS[state.settings.main], tools: [], maxTurns: 1});
  const r = JSON.parse(out.match(/\{[\s\S]*\}/)?.[0] || '{}');
  if (r.go === 'thread' && state.threads[r.id] && !state.threads[r.id].closed) return r;
  if (r.go === 'answer') return r;
  return {go: 'new', name: r.name || text.split(/\s+/).slice(0, 4).join(' '), folder: r.folder || 'topc'};
}

async function answer(text) {
  const {out} = await ask(`Answer Kamal briefly, in plain words. Look it up in the TOPC brain (Basic Memory, project "topc") first and say where the answer came from. If the brain has nothing, say so.\n\n${text}`,
    {model: MODELS[state.settings.main], mcpServers: BRAIN, allowedTools: ['mcp__basic-memory__bm_search', 'mcp__basic-memory__bm_read'], tools: [], maxTurns: 6});
  await push({kind: 'claude', text: out.trim()});
}

async function run(t, text) {
  t.state = 'working'; t.last = text.slice(0, 80); await save();
  try {
    const {out, sessionId} = await ask(text, {
      cwd: `${homedir()}/dev/${t.folder}`, resume: t.sessionId || undefined, model: MODELS[state.settings.threads],
      systemPrompt: {type: 'preset', preset: 'claude_code', append: RECEIPT}, permissionMode: 'auto',
      // shortcut: anything that would need Kamal's yes is refused until the approve buttons exist (next task)
      canUseTool: async () => ({behavior: 'deny', message: 'Unwall cannot ask Kamal for approval yet, so this was not allowed. Say so in your receipt.'}),
    });
    t.sessionId = t.sessionId || sessionId;
    t.receipt = out.match(/RECEIPT:\s*(.+)\s*$/m)?.[1].trim() || out.trim().split('\n').at(-1).slice(0, 120);
    t.state = 'done';
  } catch (e) {
    t.state = 'failed'; t.receipt = `failed: ${String(e.message || e).slice(0, 120)}`;
  }
  await push({kind: 'receipt', thread: t.id, text: t.receipt, failed: t.state === 'failed'});
}

async function send(text, quoteThread) {
  await ready;
  await push({kind: 'you', text});
  let r;
  try { r = quoteThread && state.threads[quoteThread] ? {go: 'thread', id: quoteThread} : await route(text) }
  catch (e) { r = {go: 'new', name: text.split(/\s+/).slice(0, 4).join(' '), folder: 'topc'} }
  if (r.go === 'answer') return answer(text).catch(e => push({kind: 'claude', text: `Could not answer: ${e.message}`}));
  let t = state.threads[r.id];
  if (!t) {
    const id = Date.now().toString(36);
    t = state.threads[id] = {id, name: r.name.charAt(0).toUpperCase() + r.name.slice(1), folder: r.folder, state: 'queued'};
  }
  await push({kind: 'ack', thread: t.id, fresh: !t.sessionId && t.state === 'queued'});
  busy[t.id] = (busy[t.id] || Promise.resolve()).then(() => run(t, text));
}

// one Claude turn = the tools it used plus everything it said
export function turns(messages) {
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


const body = async req => { let b = ''; for await (const c of req) b += c; return JSON.parse(b || '{}') };
const json = (res, code, o) => { res.writeHead(code, {'content-type': 'application/json', 'cache-control': 'no-store'}); res.end(JSON.stringify(o)) };

export async function one(req, res, url) {
  await ready;
  if (url.pathname === '/api/one' && req.method === 'GET') {
    const after = Number(url.searchParams.get('after') || 0);
    return json(res, 200, {stream: state.stream.filter(s => s.id > after), threads: state.threads, settings: state.settings});
  }
  if (url.pathname === '/api/one' && req.method === 'POST') {
    const {text, quote} = await body(req);
    if (typeof text !== 'string' || !text.trim()) return json(res, 400, {error: 'empty message'});
    send(text.trim(), quote).catch(e => push({kind: 'claude', text: `Something broke: ${e.message}`}));
    return json(res, 202, {ok: true}); // the box never waits for routing
  }
  if (url.pathname === '/api/one/settings' && req.method === 'POST') {
    const s = await body(req);
    for (const k of ['main', 'threads']) if (MODELS[s[k]]) state.settings[k] = s[k];
    await save(); return json(res, 200, state.settings);
  }
  const m = url.pathname.match(/^\/api\/one\/thread\/(\w+)(\/close)?$/);
  if (m && state.threads[m[1]]) {
    const t = state.threads[m[1]];
    if (m[2] && req.method === 'POST') { t.closed = true; await push({kind: 'closed', thread: t.id}); return json(res, 200, {ok: true}) }
    const msgs = t.sessionId ? await getSessionMessages(t.sessionId, {dir: `${homedir()}/dev/${t.folder}`}).catch(() => []) : [];
    return json(res, 200, {thread: t, turns: turns(msgs)});
  }
  return false;
}
