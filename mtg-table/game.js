'use strict';
// Table-mode game state. The server is authoritative: clients send actions,
// the server applies them here and sends each player a view that hides
// what that player is not allowed to see (other hands, libraries, face-down cards).

const crypto = require('crypto');

const ZONES = ['library', 'hand', 'graveyard', 'exile', 'command'];
const MAX_PLAYERS = 4;
const START_LIFE = { standard: 20, commander: 40 };
const COLORS = ['#e0a526', '#3b82f6', '#22c55e', '#ef4444'];

function id(n = 8) {
  return crypto.randomBytes(n).toString('hex');
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = crypto.randomInt(i + 1);
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function clamp(v, lo, hi) {
  v = Number(v);
  if (!Number.isFinite(v)) return lo;
  return Math.min(hi, Math.max(lo, v));
}

function cleanText(s, max = 200) {
  return String(s == null ? '' : s).replace(/[\u0000-\u001f]/g, ' ').slice(0, max);
}

function newRoom(opts = {}) {
  return {
    id: opts.id || id(16),
    name: cleanText(opts.name || 'Magic table', 60),
    format: opts.format === 'standard' ? 'standard' : 'commander',
    created: Date.now(),
    updated: Date.now(),
    players: [],        // [{pid, token, name, color, life, poison, counters, zones}]
    battlefield: [],    // [{iid, card, owner, controller, x, y, tapped, faceDown, back, counters, z}]
    cards: {},          // iid -> card instance {iid, cardId}
    db: {},             // cardId -> {name, img, backImg, backName, type, token}
    log: [],
    z: 1,
  };
}

function addLog(room, text) {
  room.log.push({ t: Date.now(), text: cleanText(text, 300) });
  if (room.log.length > 200) room.log.splice(0, room.log.length - 200);
}

function player(room, pid) {
  return room.players.find(p => p.pid === pid);
}

function join(room, name, token) {
  if (token) {
    const back = room.players.find(p => p.token === token);
    if (back) {
      if (name) back.name = cleanText(name, 30);
      return back;
    }
  }
  if (room.players.length >= MAX_PLAYERS) throw new Error('Table is full (4 players)');
  const used = new Set(room.players.map(p => p.color));
  const p = {
    pid: id(6),
    token: id(16),
    name: cleanText(name || `Player ${room.players.length + 1}`, 30),
    color: COLORS.find(c => !used.has(c)) || COLORS[0],
    life: START_LIFE[room.format],
    poison: 0,
    commanderDamage: {},
    zones: { library: [], hand: [], graveyard: [], exile: [], command: [] },
    revealHand: false,
  };
  room.players.push(p);
  addLog(room, `${p.name} joined`);
  return p;
}

function registerCard(room, c) {
  // c: {name, img, backImg, backName, type, token}
  const key = c.cardId || crypto.createHash('sha1')
    .update(`${c.name}|${c.img || ''}|${c.backImg || ''}`).digest('hex').slice(0, 16);
  if (!room.db[key]) {
    room.db[key] = {
      name: cleanText(c.name, 120),
      img: safeUrl(c.img),
      backImg: safeUrl(c.backImg),
      backName: cleanText(c.backName || '', 120),
      type: cleanText(c.type || '', 120),
      token: !!c.token,
    };
  }
  return key;
}

function safeUrl(u) {
  if (!u) return '';
  u = String(u);
  // Only Scryfall's image CDN, so a room can't be used to load arbitrary content.
  return /^https:\/\/(cards|c1|c2|svgs)\.scryfall\.io\//.test(u) ? u.slice(0, 400) : '';
}

function newInstance(room, cardId, owner) {
  const iid = id(6);
  room.cards[iid] = { iid, cardId, owner };
  return iid;
}

// Load a deck for a player: replaces their cards everywhere.
function loadDeck(room, pid, deck) {
  const p = player(room, pid);
  if (!p) throw new Error('Not in this room');
  if (!Array.isArray(deck.main) || deck.main.length > 400) throw new Error('Bad deck');
  removePlayerCards(room, pid);
  for (const z of ZONES) p.zones[z] = [];
  const add = (list, zone) => {
    for (const entry of list || []) {
      const qty = clamp(entry.qty || 1, 1, 99);
      const cardId = registerCard(room, entry);
      for (let i = 0; i < qty; i++) p.zones[zone].push(newInstance(room, cardId, pid));
    }
  };
  add(deck.main, 'library');
  add(deck.commander, 'command');
  shuffle(p.zones.library);
  p.life = START_LIFE[room.format];
  p.poison = 0;
  p.commanderDamage = {};
  addLog(room, `${p.name} loaded a deck (${p.zones.library.length} cards${p.zones.command.length ? `, ${p.zones.command.length} in command zone` : ''})`);
}

function removePlayerCards(room, pid) {
  room.battlefield = room.battlefield.filter(b => {
    if (room.cards[b.iid].owner === pid) { delete room.cards[b.iid]; return false; }
    return true;
  });
  const p = player(room, pid);
  if (p) for (const z of ZONES) for (const iid of p.zones[z]) delete room.cards[iid];
}

// Find where a card instance is: {zone:'battlefield'} or {zone, pid}
function locate(room, iid) {
  if (room.battlefield.some(b => b.iid === iid)) return { zone: 'battlefield' };
  for (const p of room.players) {
    for (const z of ZONES) if (p.zones[z].includes(iid)) return { zone: z, pid: p.pid };
  }
  return null;
}

function detach(room, iid) {
  const loc = locate(room, iid);
  if (!loc) throw new Error('Card not found');
  if (loc.zone === 'battlefield') {
    const b = room.battlefield.find(x => x.iid === iid);
    room.battlefield = room.battlefield.filter(x => x.iid !== iid);
    return { loc, b };
  }
  const zone = player(room, loc.pid).zones[loc.zone];
  zone.splice(zone.indexOf(iid), 1);
  return { loc };
}

function cardName(room, iid, hidden) {
  const c = room.cards[iid];
  if (!c || hidden) return 'a card';
  return room.db[c.cardId].name;
}

// Who may move a card: anyone may move cards on the battlefield (like a real
// table, so you can help a friend). Only the owner may move cards out of
// their own hidden zones (library, hand).
function canTouch(room, pid, iid) {
  const loc = locate(room, iid);
  if (!loc) return false;
  if (loc.zone === 'battlefield') return true;
  if (loc.zone === 'library' || loc.zone === 'hand') return loc.pid === pid;
  return true;
}

function apply(room, pid, a) {
  const p = player(room, pid);
  if (!p) throw new Error('Not in this room');
  room.updated = Date.now();
  switch (a.type) {
    case 'deck': return loadDeck(room, pid, a.deck || {});
    case 'draw': {
      const n = clamp(a.n || 1, 1, 20);
      let drawn = 0;
      for (let i = 0; i < n && p.zones.library.length; i++) {
        p.zones.hand.push(p.zones.library.shift());
        drawn++;
      }
      return addLog(room, `${p.name} drew ${drawn} card${drawn === 1 ? '' : 's'}`);
    }
    case 'shuffle':
      shuffle(p.zones.library);
      return addLog(room, `${p.name} shuffled their library`);
    case 'mulligan': {
      p.zones.library.push(...p.zones.hand);
      p.zones.hand = [];
      shuffle(p.zones.library);
      for (let i = 0; i < 7 && p.zones.library.length; i++) p.zones.hand.push(p.zones.library.shift());
      return addLog(room, `${p.name} took a mulligan (drew 7)`);
    }
    case 'move': {
      const iid = String(a.iid);
      if (!canTouch(room, pid, iid)) throw new Error('You can only move your own hidden cards');
      const { loc, b } = detach(room, iid);
      const card = room.cards[iid];
      const to = a.to;
      if (to === 'battlefield') {
        const nb = b || { iid, controller: pid, tapped: false, faceDown: false, back: false, counters: {} };
        nb.x = clamp(a.x, 0, 1);
        nb.y = clamp(a.y, 0, 1);
        nb.z = ++room.z;
        if (!b) {
          nb.controller = pid;
          nb.faceDown = !!a.faceDown;
        }
        room.battlefield.push(nb);
        if (!b) addLog(room, `${p.name} put ${nb.faceDown ? 'a face-down card' : cardName(room, iid)} onto the battlefield from ${zoneLabel(loc)}`);
        return;
      }
      if (!ZONES.includes(to)) throw new Error('Bad zone');
      // Cards always go to their owner's zones, like the real game. Tokens cease to exist.
      const owner = player(room, card.owner);
      if (room.db[card.cardId].token) {
        delete room.cards[iid];
        return addLog(room, `${cardName(room, iid)} token left the battlefield`);
      }
      const dest = owner.zones[to];
      if (to === 'library' && a.position === 'bottom') dest.push(iid);
      else if (to === 'library') dest.unshift(iid);
      else dest.push(iid);
      // Name the card unless it moved between hidden zones or was face down.
      const hiddenMove = (to === 'library' || to === 'hand') && (loc.zone === 'library' || loc.zone === 'hand');
      return addLog(room, `${p.name} moved ${cardName(room, iid, hiddenMove || (b && b.faceDown))} from ${zoneLabel(loc)} to ${to === 'library' ? `${a.position === 'bottom' ? 'the bottom' : 'the top'} of the library` : to}`);
    }
    case 'pos': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      b.x = clamp(a.x, 0, 1);
      b.y = clamp(a.y, 0, 1);
      b.z = ++room.z;
      return;
    }
    case 'tap': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      b.tapped = a.tapped == null ? !b.tapped : !!a.tapped;
      return addLog(room, `${p.name} ${b.tapped ? 'tapped' : 'untapped'} ${cardName(room, b.iid, b.faceDown)}`);
    }
    case 'untapAll':
      for (const b of room.battlefield) if (b.controller === pid) b.tapped = false;
      return addLog(room, `${p.name} untapped everything`);
    case 'faceDown': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      b.faceDown = !b.faceDown;
      return addLog(room, `${p.name} turned ${b.faceDown ? 'a card face down' : `${cardName(room, b.iid)} face up`}`);
    }
    case 'transform': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      if (!room.db[room.cards[b.iid].cardId].backImg) return;
      b.back = !b.back;
      return addLog(room, `${p.name} transformed ${cardName(room, b.iid)}`);
    }
    case 'control': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      const to = player(room, String(a.pid));
      if (!b || !to) return;
      b.controller = to.pid;
      return addLog(room, `${to.name} gained control of ${cardName(room, b.iid, b.faceDown)}`);
    }
    case 'counter': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      const kind = cleanText(a.kind || '+1/+1', 20);
      const v = clamp((b.counters[kind] || 0) + clamp(a.delta, -99, 99), -99, 999);
      if (v === 0) delete b.counters[kind]; else b.counters[kind] = v;
      return;
    }
    case 'life': {
      const target = player(room, String(a.pid || pid));
      if (!target) return;
      const delta = clamp(a.delta, -999, 999);
      target.life = clamp(target.life + delta, -999, 9999);
      return addLog(room, `${target.name} ${delta >= 0 ? 'gained' : 'lost'} ${Math.abs(delta)} life (now ${target.life})`);
    }
    case 'poison': {
      p.poison = clamp(p.poison + clamp(a.delta, -10, 10), 0, 99);
      return addLog(room, `${p.name} has ${p.poison} poison`);
    }
    case 'cmdDamage': {
      const from = player(room, String(a.from));
      if (!from) return;
      const v = clamp((p.commanderDamage[from.pid] || 0) + clamp(a.delta, -21, 21), 0, 99);
      p.commanderDamage[from.pid] = v;
      p.life = clamp(p.life - clamp(a.delta, -21, 21), -999, 9999);
      return addLog(room, `${p.name} has taken ${v} commander damage from ${from.name}`);
    }
    case 'token': {
      const cardId = registerCard(room, { ...a.card, token: true });
      const iid = newInstance(room, cardId, pid);
      room.battlefield.push({ iid, controller: pid, x: clamp(a.x ?? 0.5, 0, 1), y: clamp(a.y ?? 0.7, 0, 1), tapped: false, faceDown: false, back: false, counters: {}, z: ++room.z });
      return addLog(room, `${p.name} created a ${room.db[cardId].name} token`);
    }
    case 'clone': {
      const b = room.battlefield.find(x => x.iid === String(a.iid));
      if (!b) return;
      const src = room.db[room.cards[b.iid].cardId];
      const cardId = registerCard(room, { ...src, token: true, cardId: undefined });
      const iid = newInstance(room, cardId, pid);
      room.battlefield.push({ iid, controller: pid, x: clamp(b.x + 0.03, 0, 1), y: clamp(b.y + 0.03, 0, 1), tapped: false, faceDown: false, back: false, counters: {}, z: ++room.z });
      return addLog(room, `${p.name} made a token copy of ${src.name}`);
    }
    case 'revealHand':
      p.revealHand = !p.revealHand;
      return addLog(room, `${p.name} ${p.revealHand ? 'revealed' : 'hid'} their hand`);
    case 'reveal': {
      const iid = String(a.iid);
      if (!canTouch(room, pid, iid)) return;
      return addLog(room, `${p.name} revealed ${cardName(room, iid)}`);
    }
    case 'roll': {
      const sides = clamp(a.sides || 6, 2, 100);
      return addLog(room, `${p.name} rolled a d${sides}: ${crypto.randomInt(sides) + 1}`);
    }
    case 'coin':
      return addLog(room, `${p.name} flipped a coin: ${crypto.randomInt(2) ? 'heads' : 'tails'}`);
    case 'chat':
      return addLog(room, `${p.name}: ${cleanText(a.text, 240)}`);
    case 'rename':
      p.name = cleanText(a.name, 30) || p.name;
      return;
    default:
      throw new Error('Unknown action');
  }
}

function zoneLabel(loc) {
  return loc.zone === 'battlefield' ? 'the battlefield' : `${loc.zone === 'library' ? 'their library' : loc.zone}`;
}

// The view a given player gets. Hidden zones of others are counts only.
function view(room, pid) {
  const cardOut = (iid, show) => {
    const c = room.cards[iid];
    return show ? { iid, cardId: c.cardId, owner: c.owner } : { iid, owner: c.owner, hidden: true };
  };
  const usedIds = new Set();
  const players = room.players.map(p => {
    const me = p.pid === pid;
    const z = {};
    for (const name of ZONES) {
      const show = name === 'graveyard' || name === 'exile' || name === 'command' || (name === 'hand' && (me || p.revealHand));
      if (name === 'library') z.library = { count: p.zones.library.length };
      else z[name] = p.zones[name].map(iid => {
        if (show) usedIds.add(room.cards[iid].cardId);
        return cardOut(iid, show);
      });
    }
    return {
      pid: p.pid, name: p.name, color: p.color, life: p.life, poison: p.poison,
      commanderDamage: p.commanderDamage, revealHand: p.revealHand, zones: z,
    };
  });
  const battlefield = room.battlefield.map(b => {
    const c = room.cards[b.iid];
    const show = !b.faceDown || c.owner === pid;
    if (show) usedIds.add(c.cardId);
    return { ...b, owner: c.owner, cardId: show ? c.cardId : null };
  });
  const db = {};
  for (const k of usedIds) db[k] = room.db[k];
  return {
    room: { id: room.id, name: room.name, format: room.format },
    me: pid,
    players, battlefield, db,
    log: room.log.slice(-60),
  };
}

// Owner-only peek at the top N of their own library (for scry/surveil/searching).
function peekLibrary(room, pid, n) {
  const p = player(room, pid);
  if (!p) return [];
  const list = n === 'all' ? p.zones.library : p.zones.library.slice(0, clamp(n, 1, 100));
  return list.map(iid => ({ iid, cardId: room.cards[iid].cardId, ...room.db[room.cards[iid].cardId] }));
}

module.exports = { newRoom, join, apply, view, peekLibrary, loadDeck, locate, MAX_PLAYERS, ZONES };
