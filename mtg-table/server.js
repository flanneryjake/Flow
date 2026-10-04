'use strict';
// MTG Table server: static web client + WebSocket game rooms.
//
// Access model: only people with a link can play.
//   - Creating a room needs the host key (MTG_HOST_KEY), so only Jake (or whoever
//     has /?key=...) can open new tables.
//   - Each room lives at /r/<32-hex id>. Anyone with that link can join, nobody
//     can guess one.
//
// Env: PORT (default 8795), HOST (default 0.0.0.0), MTG_HOST_KEY (required),
//      MTG_DATA_DIR (default ./data).

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { WebSocketServer } = require('ws');
const game = require('./game');

const PORT = Number(process.env.PORT || 8795);
const HOST = process.env.HOST || '0.0.0.0';
const HOST_KEY = process.env.MTG_HOST_KEY || '';
const DATA_DIR = process.env.MTG_DATA_DIR || path.join(__dirname, 'data');
const PUBLIC = path.join(__dirname, 'public');
const ROOM_TTL_MS = 30 * 24 * 3600 * 1000;
const MAX_ROOMS = 200;

if (!HOST_KEY || HOST_KEY.length < 12) {
  console.error('Set MTG_HOST_KEY to a long random string (12+ characters) before starting.');
  process.exit(1);
}

fs.mkdirSync(DATA_DIR, { recursive: true });
const ROOMS_FILE = path.join(DATA_DIR, 'rooms.json');
const rooms = new Map();
try {
  for (const r of JSON.parse(fs.readFileSync(ROOMS_FILE, 'utf8'))) rooms.set(r.id, r);
  console.log(`Loaded ${rooms.size} room(s)`);
} catch { /* first run */ }

let dirty = false;
function save() {
  if (!dirty) return;
  dirty = false;
  const now = Date.now();
  for (const [k, r] of rooms) if (now - r.updated > ROOM_TTL_MS) rooms.delete(k);
  const tmp = ROOMS_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify([...rooms.values()]));
  fs.renameSync(tmp, ROOMS_FILE);
}
setInterval(save, 5000).unref();
process.on('SIGINT', () => { save(); process.exit(0); });
process.on('SIGTERM', () => { save(); process.exit(0); });

function keyOk(k) {
  const a = Buffer.from(String(k || ''));
  const b = Buffer.from(HOST_KEY);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };
const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "default-src 'self'; img-src 'self' https://*.scryfall.io data:; connect-src 'self' ws: wss: https://api.scryfall.com; style-src 'self' 'unsafe-inline'; script-src 'self'",
};

function send(res, code, body, type = 'application/json') {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store', ...SECURITY_HEADERS });
  res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

function readBody(req, limit = 10000) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', c => { data += c; if (data.length > limit) { reject(new Error('too big')); req.destroy(); } });
    req.on('end', () => { try { resolve(data ? JSON.parse(data) : {}); } catch (e) { reject(e); } });
  });
}

function serveFile(res, file) {
  fs.readFile(file, (err, buf) => {
    if (err) return send(res, 404, 'Not found', 'text/plain');
    send(res, 200, buf, MIME[path.extname(file)] || 'application/octet-stream');
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  try {
    if (req.method === 'POST' && url.pathname === '/api/rooms') {
      const body = await readBody(req);
      if (!keyOk(body.key)) return send(res, 403, { error: 'Wrong host key' });
      if (rooms.size >= MAX_ROOMS) return send(res, 429, { error: 'Too many rooms' });
      const room = game.newRoom({ name: body.name, format: body.format });
      rooms.set(room.id, room);
      dirty = true;
      return send(res, 200, { id: room.id, url: `/r/${room.id}` });
    }
    if (req.method === 'POST' && url.pathname === '/api/key') {
      const body = await readBody(req);
      return send(res, keyOk(body.key) ? 200 : 403, { ok: keyOk(body.key) });
    }
    if (req.method === 'GET' && url.pathname === '/api/rooms') {
      if (!keyOk(url.searchParams.get('key'))) return send(res, 403, { error: 'Wrong host key' });
      return send(res, 200, [...rooms.values()]
        .sort((a, b) => b.updated - a.updated)
        .map(r => ({ id: r.id, name: r.name, format: r.format, players: r.players.map(p => p.name), updated: r.updated })));
    }
    if (req.method === 'GET' && /^\/r\/[0-9a-f]{32}$/.test(url.pathname)) {
      return serveFile(res, path.join(PUBLIC, 'table.html'));
    }
    if (req.method === 'GET' && url.pathname === '/healthz') return send(res, 200, { ok: true, rooms: rooms.size });
    if (req.method === 'GET') {
      const rel = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      const file = path.normalize(path.join(PUBLIC, rel));
      if (!file.startsWith(PUBLIC + path.sep)) return send(res, 404, 'Not found', 'text/plain');
      return serveFile(res, file);
    }
    send(res, 405, 'Method not allowed', 'text/plain');
  } catch (e) {
    send(res, 400, { error: String(e.message || e) });
  }
});

// ---- WebSocket rooms ----
const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 512 * 1024 });
const sockets = new Map(); // roomId -> Set(ws)

function broadcast(room) {
  for (const ws of sockets.get(room.id) || []) {
    if (ws.readyState === 1 && ws.pid) ws.send(JSON.stringify({ type: 'state', state: game.view(room, ws.pid) }));
  }
}

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, 'http://x');
  const room = rooms.get(url.searchParams.get('room') || '');
  if (!room) { ws.close(4004, 'Room not found'); return; }
  if (!sockets.has(room.id)) sockets.set(room.id, new Set());
  sockets.get(room.id).add(ws);

  let bucket = 40; // simple rate limit: 40 messages, refilled 20/s
  const refill = setInterval(() => { bucket = Math.min(40, bucket + 20); }, 1000);

  ws.on('message', raw => {
    if (--bucket < 0) return;
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    try {
      if (msg.type === 'join') {
        const p = game.join(room, msg.name, msg.token);
        ws.pid = p.pid;
        ws.send(JSON.stringify({ type: 'joined', pid: p.pid, token: p.token }));
      } else if (!ws.pid) {
        throw new Error('Join first');
      } else if (msg.type === 'peek') {
        const cards = game.peekLibrary(room, ws.pid, msg.n);
        ws.send(JSON.stringify({ type: 'peek', n: msg.n, cards }));
        if (msg.n === 'all') game.apply(room, ws.pid, { type: 'chat', text: '(searched their library)' });
        else return;
      } else if (msg.type === 'action') {
        game.apply(room, ws.pid, msg.action || {});
      } else return;
      dirty = true;
      broadcast(room);
    } catch (e) {
      ws.send(JSON.stringify({ type: 'error', error: String(e.message || e) }));
    }
  });
  ws.on('close', () => {
    clearInterval(refill);
    sockets.get(room.id)?.delete(ws);
  });
});

// Keep connections alive through proxies.
setInterval(() => { for (const ws of wss.clients) if (ws.readyState === 1) ws.ping(); }, 25000).unref();

server.listen(PORT, HOST, () => console.log(`MTG Table on http://${HOST}:${PORT}`));
