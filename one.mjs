// One box: every message goes to a router that answers it, sends it to an open thread, or starts a
// new one. Threads are background Claude Code sessions; the page polls /api/one for what changed.
import {readFile, writeFile, mkdir, readdir} from 'node:fs/promises';
import {homedir} from 'node:os';
import {query, getSessionMessages} from '@anthropic-ai/claude-agent-sdk';

const DIR = `${homedir()}/.unwall`;
const STATE = `${DIR}/one.json`;
const MODELS = {Haiku: 'haiku', Sonnet: 'sonnet', Opus: 'opus'};
const BRAIN = {'basic-memory': {type: 'http', url: 'http://127.0.0.1:8765/mcp'}};
const RECEIPT = `Kamal reads only the last part of your reply, so end it in one of two ways:
- "RECEIPT: " followed by the result itself in at most 3 short lines. If he asked a question, that is the answer with its facts, numbers or list, not a description of what you did. If you changed something, say what changed and where he can check it.
- "ASK: " followed by one question, only when you truly cannot go on without him: you need his yes for something risky or outward (sending, posting, paying, deleting), or the answer exists nowhere you can look. Never ask what you can find out yourself from files, tools, the TOPC brain or past sessions; decide, say what you assumed, and finish the work.`;

let state = {settings: {main: 'Sonnet', threads: 'Sonnet', where: 'air'}, threads: {}, stream: [], seq: 0};
const ready = readFile(STATE, 'utf8').then(s => { const old = JSON.parse(s); state = {...state, ...old, settings: {...state.settings, ...old.settings}} }).catch(() => {});

// Air threads: the relay on Kamal's Mac holds one request open on /api/air/next and runs each job there
const AIR_HOME = '/Users/kamal';
const air = {queue: [], wake: null, seen: 0, done: {}};
const airOnline = () => Date.now() - air.seen < 120000;
function runOnAir(job) {
  air.queue.push(job); air.wake?.();
  return new Promise(resolve => { air.done[job.id] = resolve });
}
const save = async () => { await mkdir(DIR, {recursive: true}); await writeFile(STATE, JSON.stringify(state)) };
const push = item => { state.stream.push({id: ++state.seq, at: Date.now(), ...item}); return save() };
const busy = {}; // thread id -> promise of its current run, so one thread handles one message at a time

// what was said lately, so "it" and "that" in a new message can be resolved
function recent() {
  const name = id => state.threads[id]?.name || '?';
  return state.stream.slice(-10).map(s =>
    s.kind === 'you' ? `Kamal: ${s.text.slice(0, 300)}` :
    s.kind === 'ack' ? `  (sent to thread "${name(s.thread)}")` :
    s.kind === 'receipt' ? `  thread "${name(s.thread)}" replied: ${s.text.slice(0, 200)}` :
    s.kind === 'ask' ? `  thread "${name(s.thread)}" asked Kamal: ${s.text.slice(0, 200)}` :
    s.kind === 'claude' ? `  answered: ${s.text.slice(0, 200)}` : '').filter(Boolean).join('\n') || '(nothing yet)';
}

// past desktop sessions (synced from the Air) whose titles share words with the message
const STOP = new Set('what where when which with that this from have about status still done going should would could there their them they then'.split(' '));
async function pastSessions(text) {
  const list = JSON.parse(await readFile(`${DIR}/sessions.json`, 'utf8').catch(() => '[]'));
  const flat = s => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const words = [...new Set(text.toLowerCase().match(/[a-z0-9]{4,}/g) || [])].filter(w => !STOP.has(w));
  return list.map(s => ({s, score: words.filter(w => flat(s.title).includes(w)).length}))
    .filter(x => x.score).sort((a, b) => b.score - a.score || b.s.active - a.s.active).slice(0, 6).map(x => x.s);
}
async function lastWords(s) {
  const msgs = await getSessionMessages(s.cli, {dir: s.cwd}).catch(() => []);
  return turns(msgs).slice(-4).map(t => `${t.role === 'user' ? 'Kamal' : 'Claude'}: ${t.text.slice(0, 1200)}`).join('\n');
}

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
  const all = Object.values(state.threads), open = all.filter(t => !t.closed), shut = all.filter(t => t.closed).slice(-5);
  const line = t => `- id ${t.id}: "${t.name}" in ~/dev/${t.folder}, ${t.closed ? 'closed' : t.state}: ${(t.ask || t.receipt || t.last || '').slice(0, 160)}`;
  const prompt = `You route Kamal's messages. Send a message to a thread only when it is about that thread's own subject; use the recent conversation to tell what "it" or "that" means. ` +
    `Questions about the overall picture (what is running, how many sessions or threads, what is waiting, what was decided, where something was left) are answers, even right after a thread's work.\n\nRecent conversation:\n${recent()}\n\n` +
    `Threads:\n${open.map(line).join('\n') || '(none open)'}\n${shut.map(line).join('\n')}\n\nProject folders in ~/dev: ${(await folders()).join(', ')}\n\nKamal's new message:\n"""${text}"""\n\n` +
    `Reply with JSON only, one of:\n{"go":"thread","id":"<thread id>"} when it continues a thread's work (a closed one gets reopened)\n` +
    `{"go":"new","name":"<2-4 word topic>","folder":"<one folder from the list, or topc if none fits>"} for work on a new topic\n` +
    `{"go":"answer"} for a question about the overall picture or about what was decided or where something stands, which needs no work in a project`;
  const {out} = await ask(prompt, {model: MODELS[state.settings.main], tools: [], maxTurns: 1});
  const r = JSON.parse(out.match(/\{[\s\S]*\}/)?.[0] || '{}');
  if (r.go === 'thread' && state.threads[r.id]) return r;
  if (r.go === 'answer') return r;
  return {go: 'new', name: r.name || text.split(/\s+/).slice(0, 4).join(' '), folder: r.folder || 'topc'};
}

// the whole picture: this box's threads and the desktop app's sessions
async function overview() {
  const list = JSON.parse(await readFile(`${DIR}/sessions.json`, 'utf8').catch(() => '[]'));
  const day = Date.now() - 864e5, hour = Date.now() - 36e5;
  const live = list.filter(s => !s.archived && s.active > day);
  const synced = list.length ? new Date(Math.max(...list.map(s => s.active))).toLocaleString('en-IN') : 'never';
  const threads = Object.values(state.threads).map(t => `- "${t.name}" (~/dev/${t.folder}): ${t.closed ? 'closed' : t.state}${t.ask ? `, asking: ${t.ask.slice(0, 120)}` : ''}${t.receipt ? `, last: ${t.receipt.slice(0, 120)}` : ''}`).join('\n') || '(none)';
  return `Threads in this box:\n${threads}\n\nClaude desktop sessions (list last synced from the Air at ${synced}): ${list.length} total, ${list.filter(s => s.archived).length} archived, ${live.length} not archived and active in the last 24 hours, ${list.filter(s => !s.archived && s.active > hour).length} in the last hour. Those active in the last 24 hours, newest first:\n` +
    live.sort((a, b) => b.active - a.active).slice(0, 25).map(s => `- "${s.title}" (~/${s.cwd.split('/').slice(3).join('/')}, ${new Date(s.active).toLocaleString('en-IN', {hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short'})})`).join('\n');
}

async function answer(text, re) {
  const past = await pastSessions(text);
  const notes = (await Promise.all(past.slice(0, 3).map(async s => {
    const w = await lastWords(s);
    return `Session "${s.title}" (${new Date(s.active).toDateString()}, ~/${s.cwd.split('/').slice(3).join('/')})${w ? `, how it ended:\n${w}` : ', not synced here, title only'}`;
  }))).join('\n\n');
  const prompt = `Answer Kamal in plain words, at most 5 short lines. Give the answer itself first, then where it came from.\n` +
    `Sources, in order: the whole picture below (threads here and his Claude desktop sessions), the past conversations below, then the TOPC brain (Basic Memory, project "topc"). If neither has it, say so in one line.\n\n` +
    `The whole picture right now:\n${await overview()}\n\nRecent conversation here:\n${recent()}\n\nPast conversations whose titles match:\n${notes || '(none)'}\n\nKamal's question: ${text}`;
  const {out} = await ask(prompt, {model: MODELS[state.settings.main], mcpServers: BRAIN, allowedTools: ['mcp__basic-memory__bm_search', 'mcp__basic-memory__bm_read'], tools: [], maxTurns: 6});
  await push({kind: 'claude', text: out.trim(), re, sessions: past.slice(0, 3).map(s => ({title: s.title, cli: s.cli, cwd: s.cwd, active: s.active}))});
}

async function run(t, text) {
  t.state = 'working'; t.last = text.slice(0, 80); await save();
  try {
    const where = t.where || state.settings.where;
    if (!t.sessionId && t.log?.length) text = `This thread moved here from the other Mac. What happened so far:\n${t.log.slice(-4).map(l => `${l.role === 'user' ? 'Kamal' : 'You'}: ${l.text.slice(0, 1500)}`).join('\n')}\n\nKamal now: ${text}`;
    const {out, sessionId} = where === 'air'
      ? await runOnAir({id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), text, cwd: `${AIR_HOME}/dev/${t.folder}`, sessionId: t.sessionId || null, model: MODELS[state.settings.threads], append: RECEIPT})
          .then(r => { if (r.error) throw new Error(r.error); return r })
      : await ask(text, {
      cwd: `${homedir()}/dev/${t.folder}`, resume: t.sessionId || undefined, model: MODELS[state.settings.threads],
      systemPrompt: {type: 'preset', preset: 'claude_code', append: RECEIPT, snapshot: false}, permissionMode: 'auto',
      // shortcut: anything that would need Kamal's yes is refused until the approve buttons exist (next task)
      canUseTool: async () => ({behavior: 'deny', message: 'Unwall cannot ask Kamal for approval yet, so this was not allowed. Say so in your receipt.'}),
    });
    t.sessionId = t.sessionId || sessionId;
    t.ran = where;
    (t.log ||= []).push({role: 'user', text: text.replace(/^This thread moved here[\s\S]*\n\nKamal now: /, '')}, {role: 'claude', text: out});
    const asked = out.match(/^ASK:\s*([\s\S]+)$/m)?.[1].trim();
    // no marker: the reply's last paragraph is still the most useful part
    t.receipt = (out.match(/^RECEIPT:\s*([\s\S]+)$/m)?.[1] || out.trim().split(/\n\n/).at(-1)).trim().slice(0, 500);
    t.ask = asked ? asked.slice(0, 400) : null;
    t.state = asked ? 'needs' : 'done';
  } catch (e) {
    t.state = 'failed'; t.ask = null; t.receipt = `failed: ${String(e.message || e).slice(0, 120)}`;
  }
  await push(t.ask ? {kind: 'ask', thread: t.id, text: t.ask} : {kind: 'receipt', thread: t.id, text: t.receipt, failed: t.state === 'failed'});
}

async function send(text, quoteThread) {
  await ready;
  await push({kind: 'you', text});
  const re = state.seq;
  let r;
  try { r = quoteThread && state.threads[quoteThread] ? {go: 'thread', id: quoteThread} : await route(text) }
  catch (e) { r = {go: 'new', name: text.split(/\s+/).slice(0, 4).join(' '), folder: 'topc'} }
  if (r.go === 'answer') return answer(text, re).catch(e => push({kind: 'claude', text: `Could not answer: ${e.message}`}));
  let t = state.threads[r.id];
  if (!t) {
    const id = Date.now().toString(36);
    t = state.threads[id] = {id, name: r.name.charAt(0).toUpperCase() + r.name.slice(1), folder: r.folder, state: 'queued'};
  }
  const reopened = t.closed; t.closed = false; t.ask = null;
  t.where ||= state.settings.where;
  await push({kind: 'ack', thread: t.id, fresh: !t.sessionId && t.state === 'queued', reopened, where: t.where, offline: t.where === 'air' && !airOnline()});
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
    return json(res, 200, {stream: state.stream.filter(s => s.id > after), threads: state.threads, settings: state.settings, airOnline: airOnline()});
  }
  if (url.pathname === '/api/one' && req.method === 'POST') {
    const {text, quote} = await body(req);
    if (typeof text !== 'string' || !text.trim()) return json(res, 400, {error: 'empty message'});
    send(text.trim(), quote).catch(e => push({kind: 'claude', text: `Something broke: ${e.message}`}));
    return json(res, 202, {ok: true}); // the box never waits for routing
  }
  if (url.pathname === '/api/air/next') {
    air.seen = Date.now();
    if (!air.queue.length) await new Promise(r => { air.wake = r; setTimeout(r, 50000) });
    air.wake = null; air.seen = Date.now();
    const job = air.queue.shift();
    return job ? json(res, 200, job) : (res.writeHead(204), res.end());
  }
  if (url.pathname === '/api/air/done' && req.method === 'POST') {
    const r = await body(req);
    air.done[r.id]?.(r); delete air.done[r.id];
    return json(res, 200, {ok: true});
  }
  if (url.pathname === '/api/one/settings' && req.method === 'POST') {
    const s = await body(req);
    for (const k of ['main', 'threads']) if (MODELS[s[k]]) state.settings[k] = s[k];
    if (['air', 'mini'].includes(s.where)) state.settings.where = s.where;
    await save(); return json(res, 200, state.settings);
  }
  const m = url.pathname.match(/^\/api\/one\/thread\/(\w+)(\/close|\/where)?$/);
  if (m && state.threads[m[1]]) {
    const t = state.threads[m[1]];
    if (m[2] === '/close' && req.method === 'POST') { t.closed = true; await push({kind: 'closed', thread: t.id}); return json(res, 200, {ok: true}) }
    if (m[2] === '/where' && req.method === 'POST') {
      // a thread moving machines starts a fresh conversation there, briefed by what it did so far
      const {where} = await body(req);
      if (['air', 'mini'].includes(where) && where !== t.where) { t.where = where; t.sessionId = null; await save() }
      return json(res, 200, {ok: true});
    }
    if (t.log?.length) return json(res, 200, {thread: t, turns: t.log});
    const msgs = t.sessionId ? await getSessionMessages(t.sessionId, {dir: `${homedir()}/dev/${t.folder}`}).catch(() => []) : [];
    return json(res, 200, {thread: t, turns: turns(msgs)});
  }
  return false;
}
