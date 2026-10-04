'use strict';
const $ = s => document.querySelector(s);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

let key = new URLSearchParams(location.search).get('key') || store.get('mtg.hostKey') || '';
if (location.search) history.replaceState(null, '', '/');

async function unlock(k) {
  const res = await fetch('/api/key', { method: 'POST', body: JSON.stringify({ key: k }) });
  if (!res.ok) { $('#err').textContent = 'That host key is wrong.'; return; }
  key = k;
  store.set('mtg.hostKey', k);
  $('#keybox').hidden = true;
  $('#host').hidden = false;
  $('#err').textContent = '';
  listRooms();
}

async function listRooms() {
  const res = await fetch(`/api/rooms?key=${encodeURIComponent(key)}`);
  const rooms = res.ok ? await res.json() : [];
  const ul = $('#rooms');
  ul.innerHTML = '';
  if (!rooms.length) ul.innerHTML = '<li class="muted">No tables yet.</li>';
  for (const r of rooms) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = `/r/${r.id}`;
    a.textContent = r.name;
    const meta = document.createElement('span');
    meta.className = 'muted small';
    meta.textContent = ` ${r.format} · ${r.players.join(', ') || 'empty'} · ${new Date(r.updated).toLocaleString()}`;
    li.append(a, meta);
    ul.append(li);
  }
}

$('#unlock').onclick = () => unlock($('#key').value.trim());
$('#key').onkeydown = e => { if (e.key === 'Enter') unlock($('#key').value.trim()); };
$('#create').onclick = async () => {
  const res = await fetch('/api/rooms', {
    method: 'POST',
    body: JSON.stringify({ key, name: $('#rname').value, format: $('#format').value }),
  });
  const data = await res.json();
  if (!res.ok) { $('#err').textContent = data.error; return; }
  location.href = data.url;
};

if (key) unlock(key);
