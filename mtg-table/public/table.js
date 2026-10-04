'use strict';
// MTG Table client: renders the server's view and sends actions.

const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const roomId = location.pathname.split('/').pop();
let ws, state = null, me = null, peekCards = null;
let selected = null;

// ---------- connection ----------
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?room=${roomId}`);
  ws.onopen = () => {
    const token = store.get(`mtg.token.${roomId}`);
    const name = store.get('mtg.name');
    if (token || name) ws.send(JSON.stringify({ type: 'join', name, token }));
    else askName();
  };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'joined') { me = msg.pid; store.set(`mtg.token.${roomId}`, msg.token); }
    else if (msg.type === 'state') { state = msg.state; render(); }
    else if (msg.type === 'peek') { peekCards = msg.cards; showPeek(msg.n); }
    else if (msg.type === 'error') toast(msg.error);
  };
  ws.onclose = ev => {
    if (ev.code === 4004) { document.body.innerHTML = '<main class="lobby-card"><h1>Room not found</h1><p>This table link is wrong or the table was closed.</p></main>'; return; }
    toast('Disconnected, reconnecting…');
    setTimeout(connect, 1500);
  };
}

function askName() {
  const dlg = $('#joinDlg');
  $('#joinName').value = store.get('mtg.name') || '';
  dlg.showModal();
  dlg.onclose = () => {
    const name = $('#joinName').value.trim() || 'Player';
    store.set('mtg.name', name);
    ws.send(JSON.stringify({ type: 'join', name }));
  };
}

function act(action) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'action', action }));
}

// ---------- helpers ----------
function card(cardId) { return (state && state.db[cardId]) || null; }
function playerById(pid) { return state.players.find(p => p.pid === pid); }
function mePlayer() { return state && playerById(me); }
function flipped() {
  // Two-player games: seat 2 sees the table rotated so their cards are at the bottom.
  return state.players.length === 2 && state.players[1].pid === me;
}
function cardSize() {
  const bf = $('#battlefield');
  const w = Math.max(56, Math.min(100, bf.clientWidth / 13));
  return { w, h: Math.round(w * 1.395) };
}
function toTable(clientX, clientY) {
  const r = $('#battlefield').getBoundingClientRect();
  let x = (clientX - r.left) / r.width, y = (clientY - r.top) / r.height;
  if (flipped()) { x = 1 - x; y = 1 - y; }
  return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
}
function fromTable(x, y) {
  const r = $('#battlefield');
  if (flipped()) { x = 1 - x; y = 1 - y; }
  return { left: x * r.clientWidth, top: y * r.clientHeight };
}

let toastTimer;
function toast(text) {
  let t = $('#toast');
  if (!t) { t = el('div', 'toast'); t.id = 'toast'; document.body.append(t); }
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
}

// A card face element. data = {cardId?, hidden?, faceDown?, back?}
function face(data, opts = {}) {
  const div = el('div', 'card');
  const c = data.cardId ? card(data.cardId) : null;
  const showBackFace = data.back && c && c.backImg;
  if (!c || data.hidden || (data.faceDown && !opts.peekFaceDown)) {
    div.classList.add('back');
  } else {
    const src = showBackFace ? c.backImg : c.img;
    if (src) {
      const img = el('img');
      img.src = src; img.alt = c.name; img.draggable = false; img.loading = 'lazy';
      div.append(img);
    } else {
      div.classList.add('text');
      div.append(el('b', '', showBackFace ? c.backName : c.name), el('span', 'small', c.type));
    }
    if (data.faceDown) div.classList.add('peeked');
    div.onmouseenter = () => showPreview(c, showBackFace);
  }
  if (c && c.token) div.classList.add('token');
  return div;
}

function showPreview(c, backSide) {
  const p = $('#preview');
  p.innerHTML = '';
  const src = backSide ? c.backImg : c.img;
  if (src) { const img = el('img'); img.src = src; img.alt = c.name; p.append(img); }
  else p.append(el('h3', '', c.name), el('p', 'muted', c.type));
}

// ---------- rendering ----------
function render() {
  if (!state) return;
  $('#roomName').textContent = state.room.name;
  document.title = `${state.room.name} · MTG Table`;
  renderPlayers();
  renderOpponents();
  renderBattlefield();
  renderMine();
  renderLog();
}

function renderPlayers() {
  const box = $('#players');
  box.innerHTML = '';
  for (const p of state.players) {
    const chip = el('div', 'pchip');
    chip.style.borderColor = p.color;
    chip.append(el('span', 'pname', p.name + (p.pid === me ? ' (you)' : '')));
    const minus = el('button', 'tiny', '−'); minus.onclick = () => act({ type: 'life', pid: p.pid, delta: -1 });
    const life = el('span', 'life', String(p.life));
    life.title = 'Click to set life';
    life.onclick = () => {
      const v = prompt(`Set ${p.name}'s life`, p.life);
      if (v != null && v.trim() !== '' && !isNaN(Number(v))) act({ type: 'life', pid: p.pid, delta: Number(v) - p.life });
    };
    const plus = el('button', 'tiny', '+'); plus.onclick = () => act({ type: 'life', pid: p.pid, delta: 1 });
    chip.append(minus, life, plus);
    if (p.poison) chip.append(el('span', 'poison', `☠ ${p.poison}`));
    const cmd = Object.entries(p.commanderDamage || {}).filter(([, v]) => v > 0);
    for (const [from, v] of cmd) {
      const src = playerById(from);
      if (src) { const s = el('span', 'cmddmg', `⚔ ${v}`); s.title = `Commander damage from ${src.name}`; s.style.color = src.color; chip.append(s); }
    }
    box.append(chip);
  }
}

function pileEl(p, zone, label) {
  const list = zone === 'library' ? null : p.zones[zone];
  const count = zone === 'library' ? p.zones.library.count : list.length;
  const pile = el('div', `pile pile-${zone}`);
  pile.dataset.zone = zone;
  pile.dataset.pid = p.pid;
  if (zone !== 'library' && list.length) {
    const top = list[list.length - 1];
    pile.append(face(top));
  } else if (zone === 'library' && count) {
    pile.append(face({ hidden: true }));
  }
  pile.append(el('span', 'pilelabel', `${label} ${count}`));
  if (zone === 'library' && p.pid === me) {
    pile.onclick = () => act({ type: 'draw', n: 1 });
    pile.oncontextmenu = e => { e.preventDefault(); libraryMenu(e); };
    pile.title = 'Click to draw, right-click for more';
  } else if (zone !== 'library') {
    pile.onclick = () => showZone(p, zone);
  }
  return pile;
}

function renderOpponents() {
  const box = $('#opponents');
  box.innerHTML = '';
  for (const p of state.players) {
    if (p.pid === me) continue;
    const row = el('div', 'opp');
    row.style.borderColor = p.color;
    row.append(el('span', 'oppname', p.name));
    const handBox = el('div', 'opphand');
    for (const h of p.zones.hand) { const f = face(h); f.classList.add('mini'); handBox.append(f); }
    if (!p.zones.hand.length) handBox.append(el('span', 'muted small', 'no cards in hand'));
    row.append(handBox, pileEl(p, 'library', 'Library'), pileEl(p, 'graveyard', 'Grave'), pileEl(p, 'exile', 'Exile'), pileEl(p, 'command', 'Command'));
    box.append(row);
  }
  if (state.players.length < 2) box.append(el('p', 'muted small waiting', 'Waiting for friends: click "Copy invite link" and send it to them.'));
}

const bfEls = new Map();
function renderBattlefield() {
  const bf = $('#battlefield');
  const { w, h } = cardSize();
  bf.style.setProperty('--cw', `${w}px`);
  bf.style.setProperty('--ch', `${h}px`);
  const seen = new Set();
  for (const b of state.battlefield) {
    seen.add(b.iid);
    let node = bfEls.get(b.iid);
    const sig = JSON.stringify([b.cardId, b.faceDown, b.back, b.tapped, b.counters, b.controller]);
    if (!node || node.dataset.sig !== sig) {
      const fresh = face(b, { peekFaceDown: b.owner === me });
      fresh.classList.add('bf');
      fresh.dataset.iid = b.iid;
      fresh.dataset.sig = sig;
      if (b.tapped) fresh.classList.add('tapped');
      const ctl = playerById(b.controller);
      if (ctl) fresh.style.setProperty('--owner', ctl.color);
      const counters = Object.entries(b.counters || {});
      if (counters.length) {
        const cbox = el('div', 'counters');
        for (const [k, v] of counters) {
          const sign = v > 0 ? '+' : '';
          cbox.append(el('span', 'counter', k === '+1/+1' ? `${sign}${v}/${sign}${v}` : `${k} ${v}`));
        }
        fresh.append(cbox);
      }
      bindBattlefieldCard(fresh, b.iid);
      if (node) node.replaceWith(fresh); else bf.append(fresh);
      node = fresh;
      bfEls.set(b.iid, node);
    }
    if (!node.classList.contains('dragging')) {
      const pos = fromTable(b.x, b.y);
      node.style.left = `${pos.left - w / 2}px`;
      node.style.top = `${pos.top - h / 2}px`;
    }
    node.style.zIndex = b.z;
    node.classList.toggle('selected', selected === b.iid);
  }
  for (const [iid, node] of bfEls) if (!seen.has(iid)) { node.remove(); bfEls.delete(iid); }
}

function renderMine() {
  const p = mePlayer();
  const piles = $('#myPiles');
  const hand = $('#hand');
  piles.innerHTML = '';
  hand.innerHTML = '';
  if (!p) return;
  piles.append(pileEl(p, 'library', 'Library'), pileEl(p, 'graveyard', 'Grave'), pileEl(p, 'exile', 'Exile'), pileEl(p, 'command', 'Command'));
  for (const h of p.zones.hand) {
    const f = face(h);
    f.classList.add('inhand');
    f.dataset.iid = h.iid;
    bindHandCard(f, h.iid);
    hand.append(f);
  }
  if (!p.zones.hand.length && !p.zones.library.count && !state.battlefield.some(b => b.owner === me)) {
    hand.append(el('p', 'muted small', 'Click "Load deck" to get started.'));
  }
}

function renderLog() {
  const log = $('#log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.innerHTML = '';
  for (const l of state.log) {
    const line = el('div', 'logline');
    line.append(el('span', 'time', new Date(l.t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })), document.createTextNode(' ' + l.text));
    log.append(line);
  }
  if (atBottom) log.scrollTop = log.scrollHeight;
}

// ---------- drag and drop ----------
function dropTarget(clientX, clientY, dragged) {
  for (const t of document.elementsFromPoint(clientX, clientY)) {
    if (t === dragged || dragged.contains(t)) continue;
    const pile = t.closest('.pile');
    if (pile && pile.dataset.pid === me) return { zone: pile.dataset.zone };
    if (pile) return { zone: pile.dataset.zone, other: true };
    if (t.closest('#hand')) return { zone: 'hand' };
    if (t.closest('#battlefield')) return { zone: 'battlefield' };
  }
  return null;
}

function startDrag(node, e, onDrop, onClick) {
  if (e.button !== 0) return;
  const startX = e.clientX, startY = e.clientY;
  const rect = node.getBoundingClientRect();
  const offX = startX - rect.left, offY = startY - rect.top;
  let ghost = null;
  const move = ev => {
    if (!ghost && Math.hypot(ev.clientX - startX, ev.clientY - startY) < 5) return;
    if (!ghost) {
      ghost = node.cloneNode(true);
      ghost.classList.add('ghost');
      ghost.style.width = `${rect.width}px`;
      ghost.style.height = `${rect.height}px`;
      document.body.append(ghost);
      node.classList.add('dragging');
    }
    ghost.style.left = `${ev.clientX - offX}px`;
    ghost.style.top = `${ev.clientY - offY}px`;
  };
  const up = ev => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    if (!ghost) { onClick && onClick(ev); return; }
    ghost.remove();
    node.classList.remove('dragging');
    const target = dropTarget(ev.clientX, ev.clientY, node);
    // Drop position = where the card's center ends up.
    const cx = ev.clientX - offX + rect.width / 2, cy = ev.clientY - offY + rect.height / 2;
    onDrop(target, toTable(cx, cy), ev);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

function moveTo(iid, target, pos, extra = {}) {
  if (!target) return render();
  if (target.zone === 'battlefield') return act({ type: 'move', iid, to: 'battlefield', x: pos.x, y: pos.y, ...extra });
  if (target.other) { toast('Cards go to their owner\'s piles. Drop it on yours.'); return render(); }
  act({ type: 'move', iid, to: target.zone, ...extra });
}

function bindBattlefieldCard(node, iid) {
  node.onpointerdown = e => startDrag(node, e, (target, pos) => {
    if (target && target.zone === 'battlefield') act({ type: 'pos', iid, x: pos.x, y: pos.y });
    else moveTo(iid, target, pos);
  }, () => { selected = iid; render(); });
  node.ondblclick = () => act({ type: 'tap', iid });
  node.oncontextmenu = e => { e.preventDefault(); battlefieldMenu(e, iid); };
}

function bindHandCard(node, iid) {
  node.onpointerdown = e => startDrag(node, e, (target, pos) => moveTo(iid, target, pos));
  node.ondblclick = () => act({ type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.72 });
  node.oncontextmenu = e => { e.preventDefault(); handMenu(e, iid); };
}

// ---------- menus ----------
function openMenu(e, items) {
  const m = $('#menu');
  m.innerHTML = '';
  for (const it of items) {
    if (it === '-') { m.append(el('hr')); continue; }
    const b = el('button', '', it.label);
    b.onclick = () => { closeMenu(); it.fn(); };
    m.append(b);
  }
  m.hidden = false;
  const x = Math.min(e.clientX, window.innerWidth - 220), y = Math.min(e.clientY, window.innerHeight - m.offsetHeight - 8);
  m.style.left = `${x}px`; m.style.top = `${Math.max(8, y)}px`;
}
function closeMenu() { $('#menu').hidden = true; }
document.addEventListener('pointerdown', e => { if (!e.target.closest('#menu')) closeMenu(); });

function zoneMoves(iid) {
  return [
    { label: 'To hand', fn: () => act({ type: 'move', iid, to: 'hand' }) },
    { label: 'To graveyard', fn: () => act({ type: 'move', iid, to: 'graveyard' }) },
    { label: 'To exile', fn: () => act({ type: 'move', iid, to: 'exile' }) },
    { label: 'To top of library', fn: () => act({ type: 'move', iid, to: 'library', position: 'top' }) },
    { label: 'To bottom of library', fn: () => act({ type: 'move', iid, to: 'library', position: 'bottom' }) },
    { label: 'To command zone', fn: () => act({ type: 'move', iid, to: 'command' }) },
  ];
}

function battlefieldMenu(e, iid) {
  const b = state.battlefield.find(x => x.iid === iid);
  if (!b) return;
  const c = card(b.cardId);
  const items = [
    { label: b.tapped ? 'Untap' : 'Tap', fn: () => act({ type: 'tap', iid }) },
    { label: '+1/+1 counter', fn: () => act({ type: 'counter', iid, kind: '+1/+1', delta: 1 }) },
    { label: '−1/−1 (remove +1/+1)', fn: () => act({ type: 'counter', iid, kind: '+1/+1', delta: -1 }) },
    { label: 'Other counter…', fn: () => {
      const kind = prompt('Counter name (loyalty, charge, -1/-1, …)', 'loyalty');
      if (!kind) return;
      const n = Number(prompt(`How many ${kind} counters to add? (negative removes)`, '1'));
      if (n) act({ type: 'counter', iid, kind, delta: n });
    } },
    '-',
    { label: b.faceDown ? 'Turn face up' : 'Turn face down', fn: () => act({ type: 'faceDown', iid }) },
  ];
  if (c && c.backImg) items.push({ label: 'Transform / flip side', fn: () => act({ type: 'transform', iid }) });
  items.push({ label: 'Make a token copy', fn: () => act({ type: 'clone', iid }) });
  for (const p of state.players) if (p.pid !== b.controller) items.push({ label: `Give control to ${p.name}`, fn: () => act({ type: 'control', iid, pid: p.pid }) });
  items.push('-', ...zoneMoves(iid));
  openMenu(e, items);
}

function handMenu(e, iid) {
  openMenu(e, [
    { label: 'Play to battlefield', fn: () => act({ type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.72 }) },
    { label: 'Play face down', fn: () => act({ type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.72, faceDown: true }) },
    { label: 'Reveal to table', fn: () => act({ type: 'reveal', iid }) },
    '-',
    ...zoneMoves(iid).filter(m => m.label !== 'To hand'),
  ]);
}

function libraryMenu(e) {
  openMenu(e, [
    { label: 'Draw 1', fn: () => act({ type: 'draw', n: 1 }) },
    { label: 'Draw 7 (opening hand)', fn: () => act({ type: 'draw', n: 7 }) },
    { label: 'Mulligan (shuffle hand in, draw 7)', fn: () => act({ type: 'mulligan' }) },
    { label: 'Shuffle', fn: () => act({ type: 'shuffle' }) },
    '-',
    { label: 'Look at top 1', fn: () => peek(1) },
    { label: 'Look at top 3', fn: () => peek(3) },
    { label: 'Look at top X…', fn: () => { const n = Number(prompt('How many?', '5')); if (n) peek(n); } },
    { label: 'Search library', fn: () => peek('all') },
    { label: 'Mill 1', fn: () => millTop() },
  ]);
}

function peek(n) { ws.send(JSON.stringify({ type: 'peek', n })); }
function millTop() {
  // Look at the top card, then move it; the server tells us its id.
  pendingMill = true;
  peek(1);
}
let pendingMill = false;

// ---------- zone viewers ----------
function cardButtons(iid, ownerIsMe, fromLibrary) {
  const box = el('div', 'zbtns');
  const add = (label, action) => { const b = el('button', 'tiny', label); b.type = 'button'; b.onclick = () => { act(action); $('#zoneDlg').close(); }; box.append(b); };
  add('Battlefield', { type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.72 });
  if (ownerIsMe) add('Hand', { type: 'move', iid, to: 'hand' });
  add('Grave', { type: 'move', iid, to: 'graveyard' });
  add('Exile', { type: 'move', iid, to: 'exile' });
  if (ownerIsMe) {
    add('Top', { type: 'move', iid, to: 'library', position: 'top' });
    add('Bottom', { type: 'move', iid, to: 'library', position: 'bottom' });
  }
  return box;
}

function showZone(p, zone) {
  $('#zoneTitle').textContent = `${p.name}: ${zone} (${p.zones[zone].length})`;
  const box = $('#zoneCards');
  box.innerHTML = '';
  for (const c of [...p.zones[zone]].reverse()) {
    const wrap = el('div', 'zcard');
    wrap.append(face(c));
    if (!c.hidden) wrap.append(cardButtons(c.iid, c.owner === me));
    box.append(wrap);
  }
  if (!p.zones[zone].length) box.append(el('p', 'muted', 'Empty.'));
  $('#zoneDlg').showModal();
}

function showPeek(n) {
  if (pendingMill) {
    pendingMill = false;
    if (peekCards[0]) act({ type: 'move', iid: peekCards[0].iid, to: 'graveyard' });
    return;
  }
  // Peeked cards aren't in the shared db yet; add them locally for rendering.
  for (const c of peekCards) state.db[c.cardId] = { name: c.name, img: c.img, backImg: c.backImg, backName: c.backName, type: c.type, token: c.token };
  $('#zoneTitle').textContent = n === 'all' ? `Your library (${peekCards.length}), top first` : `Top ${peekCards.length} of your library`;
  const box = $('#zoneCards');
  box.innerHTML = '';
  for (const c of peekCards) {
    const wrap = el('div', 'zcard');
    wrap.append(face({ cardId: c.cardId }), cardButtons(c.iid, true, true));
    box.append(wrap);
  }
  if (!peekCards.length) box.append(el('p', 'muted', 'Your library is empty.'));
  const dlg = $('#zoneDlg');
  dlg.onclose = () => { if (n === 'all') { dlg.onclose = null; if (confirm('Shuffle your library?')) act({ type: 'shuffle' }); } };
  dlg.showModal();
}

// ---------- deck loading ----------
$('#deckBtn').onclick = () => { $('#deckStatus').textContent = ''; $('#deckDlg').showModal(); };
$('#deckGo').onclick = async e => {
  e.preventDefault();
  const parsed = Deck.parseDeck($('#deckText').value);
  const total = parsed.main.reduce((s, c) => s + c.qty, 0) + parsed.commander.reduce((s, c) => s + c.qty, 0);
  if (!total) { $('#deckStatus').textContent = 'No cards found in that list.'; return; }
  $('#deckGo').disabled = true;
  const { deck, missing } = await Deck.resolveDeck(parsed, (done, all) => { $('#deckStatus').textContent = `Looking up cards on Scryfall… ${done}/${all}`; });
  $('#deckGo').disabled = false;
  act({ type: 'deck', deck });
  store.set('mtg.lastDeck', $('#deckText').value);
  $('#deckDlg').close();
  toast(missing.length ? `Loaded ${total} cards. Not found on Scryfall (shown as text): ${missing.slice(0, 5).join(', ')}${missing.length > 5 ? '…' : ''}` : `Loaded ${total} cards. Click your library to draw.`);
};
$('#deckText').value = store.get('mtg.lastDeck') || '';

// ---------- toolbar ----------
$('#invite').onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); toast('Invite link copied. Send it to your friends.'); }
  catch { prompt('Copy this link:', location.href); }
};
$('#drawBtn').onclick = () => act({ type: 'draw', n: 1 });
$('#untapBtn').onclick = () => act({ type: 'untapAll' });
$('#moreBtn').onclick = e => {
  const p = mePlayer();
  const items = [
    { label: 'Create token…', fn: async () => {
      const name = prompt('Token name (Treasure, Soldier, Myr, Clue…)', 'Treasure');
      if (!name) return;
      const c = await Deck.findToken(name);
      act({ type: 'token', card: c, x: 0.5, y: 0.65 });
    } },
    { label: 'Roll d6', fn: () => act({ type: 'roll', sides: 6 }) },
    { label: 'Roll d20', fn: () => act({ type: 'roll', sides: 20 }) },
    { label: 'Flip a coin', fn: () => act({ type: 'coin' }) },
    '-',
    { label: 'Poison +1', fn: () => act({ type: 'poison', delta: 1 }) },
    { label: 'Poison −1', fn: () => act({ type: 'poison', delta: -1 }) },
  ];
  for (const o of state.players) if (o.pid !== me) items.push({ label: `Took commander damage from ${o.name}…`, fn: () => { const n = Number(prompt('How much?', '1')); if (n) act({ type: 'cmdDamage', from: o.pid, delta: n }); } });
  items.push('-', { label: p && p.revealHand ? 'Hide my hand' : 'Reveal my hand to everyone', fn: () => act({ type: 'revealHand' }) },
    { label: 'Change my name…', fn: () => { const n = prompt('Name', p ? p.name : ''); if (n) { store.set('mtg.name', n); act({ type: 'rename', name: n }); } } });
  openMenu(e, items);
};
$('#chat').onsubmit = e => {
  e.preventDefault();
  const t = $('#chatText').value.trim();
  if (t) act({ type: 'chat', text: t });
  $('#chatText').value = '';
};
document.addEventListener('keydown', e => {
  if (e.target.closest('input, textarea, dialog')) return;
  if (e.key === 'd') act({ type: 'draw', n: 1 });
  if (e.key === 'u') act({ type: 'untapAll' });
  if ((e.key === 't' || e.key === ' ') && selected) { e.preventDefault(); act({ type: 'tap', iid: selected }); }
});
window.addEventListener('resize', () => render());

connect();
