// Unwall on Cloudflare (unwall-tasks#15): the page, the message stream, threads and settings live in one
// Durable Object; all Claude work happens in the relay on Kamal's Mac, which long-polls /api/relay/next.
import {DurableObject} from 'cloudflare:workers';

const json = (o, status = 200) => new Response(JSON.stringify(o), {status, headers: {'content-type': 'application/json', 'cache-control': 'no-store'}});

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(req);
    // one key for the page and the relay; the page keeps it in localStorage
    if (req.headers.get('authorization') !== `Bearer ${env.UNWALL_KEY}`) return json({error: 'locked'}, 401);
    return env.BOX.get(env.BOX.idFromName('kamal')).fetch(req);
  },
};

export class Box extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.waiters = [];   // relay requests held open until there is work
    this.seen = 0;       // last time the relay asked for work
    this.ready = ctx.blockConcurrencyWhile(async () => {
      this.s = (await ctx.storage.get('s')) || {settings: {main: 'Sonnet', threads: 'Sonnet'}, threads: {}, stream: [], seq: 0, inbox: []};
    });
  }
  save() { return this.ctx.storage.put('s', this.s) }
  push(item) { const it = {id: ++this.s.seq, at: Date.now(), ...item}; this.s.stream.push(it); this.s.stream = this.s.stream.slice(-500); return it }
  wake() { const w = this.waiters; this.waiters = []; w.forEach(r => r(false)) }

  async fetch(req) {
    await this.ready;
    const url = new URL(req.url), p = url.pathname, s = this.s;
    const body = async () => req.method === 'POST' ? req.json().catch(() => ({})) : {};

    // the page
    if (p === '/api/one' && req.method === 'GET') {
      const after = Number(url.searchParams.get('after') || 0);
      const threads = Object.fromEntries(Object.entries(s.threads).map(([k, t]) => [k, {...t, log: undefined}]));
      return json({stream: s.stream.filter(x => x.id > after), threads, settings: s.settings, airOnline: Date.now() - this.seen < 120000});
    }
    if (p === '/api/one' && req.method === 'POST') {
      const {text, quote, revive} = await body();
      if (typeof text !== 'string' || !text.trim()) return json({error: 'empty message'}, 400);
      const you = this.push({kind: 'you', text: text.trim()});
      s.inbox.push({type: 'message', re: you.id, text: text.trim(), quote: quote || null, revive: revive?.cli ? revive : null});
      await this.save(); this.wake();
      return json({ok: true}, 202);
    }
    if (p === '/api/one/settings' && req.method === 'POST') {
      const b = await body();
      for (const k of ['main', 'threads']) if (['Haiku', 'Sonnet', 'Opus'].includes(b[k])) s.settings[k] = b[k];
      await this.save(); return json(s.settings);
    }
    const m = p.match(/^\/api\/one\/thread\/(\w+)(\/close)?$/);
    if (m && s.threads[m[1]]) {
      const t = s.threads[m[1]];
      if (m[2]) { t.closed = true; this.push({kind: 'closed', thread: t.id}); await this.save(); return json({ok: true}) }
      return json({thread: {...t, log: undefined}, turns: t.log || []});
    }

    // the relay on Kamal's Mac
    if (p === '/api/relay/next') {
      this.seen = Date.now();
      // a job handed to a relay that never confirmed it (it restarted mid-request) goes back in line
      s.inflight ||= {};
      for (const [re, j] of Object.entries(s.inflight)) if (Date.now() - j.given > 20000) { delete s.inflight[re]; s.inbox.unshift(j) }
      if (!s.inbox.length) {
        const stale = await new Promise(r => { this.waiters.forEach(w => w(true)); this.waiters = [r]; setTimeout(() => r(false), 50000) });
        if (stale) return new Response(null, {status: 204});
      }
      this.seen = Date.now();
      const job = s.inbox.shift();
      if (!job) return new Response(null, {status: 204});
      s.inflight[job.re] = {...job, given: Date.now()};
      await this.save();
      // everything the relay needs to route and answer, without a second round trip
      const threads = Object.values(s.threads).map(t => ({...t, log: undefined}));
      return json({...job, settings: s.settings, threads, recent: s.stream.slice(-12)});
    }
    if (p === '/api/relay/ack' && req.method === 'POST') {
      const {re} = await body(); delete (s.inflight ||= {})[re]; await this.save(); return json({ok: true});
    }
    if (p === '/api/relay/event' && req.method === 'POST') {
      const e = await body();
      const it = this.push(e); await this.save(); return json({id: it.id});
    }
    if (p === '/api/relay/thread' && req.method === 'POST') {
      const {thread, log} = await body();
      if (!thread?.id) return json({error: 'no thread id'}, 400);
      const t = s.threads[thread.id] = {...(s.threads[thread.id] || {}), ...thread};
      if (log?.length) t.log = (t.log || []).concat(log).slice(-60);
      await this.save(); return json({ok: true});
    }
    return json({error: 'not found'}, 404);
  }
}
