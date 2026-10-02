// Fleet panel for the phone app's System tab. Self-contained: include this file and put
// <div id="fleet-panel"></div> where the panel should appear. Talks to /api/fleet (fleet_api.py).
(function () {
  const css = `
#fleet-panel{font:inherit}
#fleet-panel .fl-card{border:1px solid var(--line,#8884);border-radius:12px;padding:10px 12px;margin:8px 0}
#fleet-panel .fl-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#fleet-panel .fl-name{font-weight:600;flex:1;min-width:8em}
#fleet-panel .fl-dot{width:10px;height:10px;border-radius:50%;display:inline-block}
#fleet-panel .fl-sub{opacity:.7;font-size:.85em;margin-top:2px}
#fleet-panel button{border:1px solid var(--line,#8886);background:transparent;color:inherit;border-radius:8px;padding:6px 10px;font:inherit}
#fleet-panel button.fl-danger{border-color:#d9534f;color:#d9534f}
#fleet-panel .fl-roles td{padding:3px 6px 3px 0;vertical-align:top}
#fleet-panel .fl-warn{color:#d9534f}
#fleet-panel .fl-sheet{border:2px solid #d9534f;border-radius:12px;padding:10px 12px;margin:8px 0}
#fleet-panel .fl-sheet ul{margin:4px 0 8px 18px;padding:0}`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const MODE_TEXT = {active: 'Online', paused: 'Paused (no new cards)', isolated: 'Disconnected'};
  let data = null, sheet = null, busy = false;

  async function load(fresh) {
    const el = document.getElementById('fleet-panel');
    if (!el) return;
    try {
      const r = await fetch('/api/fleet' + (fresh ? '?fresh=1' : ''));
      data = await r.json();
      if (!r.ok) throw new Error(data.error || r.status);
    } catch (e) {
      el.innerHTML = `<div class="fl-card fl-warn">Fleet status unavailable: ${esc(e.message)}</div>`;
      return;
    }
    render();
  }

  function dot(m) {
    const c = m.mode !== 'active' ? '#999' : m.up ? '#2e9d4f' : '#d9534f';
    return `<span class="fl-dot" style="background:${c}"></span>`;
  }

  function render() {
    const el = document.getElementById('fleet-panel');
    if (!el || !data) return;
    let h = '';
    for (const [name, m] of Object.entries(data.machines)) {
      const seen = m.minutes_ago == null ? 'never checked in' : m.minutes_ago < 1 ? 'just now' : `${Math.round(m.minutes_ago)} min ago`;
      const until = m.state && m.state.until ? ` until ${new Date(m.state.until).toLocaleString()}` : '';
      const pending = m.applied && m.applied !== m.mode ? ' · applying…' : '';
      const btns = m.mode === 'active'
        ? `<button data-act="paused" data-m="${name}">Pause</button><button class="fl-danger" data-act="isolated" data-m="${name}">Disconnect</button>`
        : `<button data-act="active" data-m="${name}">Bring back</button>`;
      h += `<div class="fl-card"><div class="fl-row">${dot(m)}<span class="fl-name">${esc(m.label)}</span>${btns}</div>
        <div class="fl-sub">${esc(MODE_TEXT[m.mode] || m.mode)}${esc(until)}${pending} · ${m.up ? 'checked in' : 'not checking in'} ${esc(seen)} · tailnet ${esc(m.tailnet)}</div>${claudeLine(m.claude)}</div>`;
      if (sheet && sheet.machine === name) h += sheetHtml();
    }
    h += '<div class="fl-card"><div class="fl-name">Who covers what</div><table class="fl-roles">';
    for (const r of Object.values(data.roles)) {
      const who = r.serving ? esc(data.machines[r.serving].label) : '<span class="fl-warn">Nobody</span>';
      const sb = r.standby.length ? ` <span class="fl-sub">(standby ${r.standby.map(n => esc(data.machines[n].label)).join(', ')})</span>` : '';
      h += `<tr><td>${esc(r.label)}</td><td>${who}${sb}</td></tr>`;
    }
    h += '</table></div>';
    el.innerHTML = h;
    el.querySelectorAll('button[data-act]').forEach(b => b.onclick = () => act(b.dataset.m, b.dataset.act));
    el.querySelectorAll('button[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
  }

  // Claude guard (jarvis/claude-guard): how many Claude processes the PC is running, red when over a cap.
  function claudeLine(c) {
    if (!c || !c.counts) return '<div class="fl-sub">Claude: no count yet (Claude guard not installed)</div>';
    const n = c.counts, gb = n.claude_mem_mb == null ? '' : ` · ${(n.claude_mem_mb / 1024).toFixed(1)} GB`;
    const stale = c.minutes_ago != null && c.minutes_ago > 15 ? ` · counted ${Math.round(c.minutes_ago)} min ago` : '';
    const txt = `Claude: ${n.sessions_total} session${n.sessions_total === 1 ? '' : 's'} (${n.rc_sessions} Remote Control, ${n.headless} -p, ${n.interactive} terminal, ${n.desktop_code} desktop)` +
      ` · ${n.listeners} RC server${n.listeners === 1 ? '' : 's'} · desktop app ${n.desktop_instances}${gb}${stale}`;
    const over = (c.over || []).length ? `<div class="fl-sub fl-warn">Over cap: ${esc(c.over.join(', '))}</div>` : '';
    return `<div class="fl-sub${(c.over || []).length ? ' fl-warn' : ''}">${esc(txt)}</div>${over}`;
  }

  function sheetHtml() {
    const p = sheet.plan, label = esc(data.machines[sheet.machine].label);
    const verb = sheet.mode === 'isolated' ? 'Disconnect' : 'Pause';
    let h = `<div class="fl-sheet"><b>${verb} ${label}?</b>`;
    if (p.moves.length) h += '<div>Moves over:</div><ul>' + p.moves.map(x => `<li>${esc(x.label)} → ${esc(data.machines[x.to].label)}</li>`).join('') + '</ul>';
    if (p.uncovered.length) h += '<div class="fl-warn">Nobody else can cover:</div><ul class="fl-warn">' + p.uncovered.map(x => `<li>${esc(x.label)}${x.note ? ' (' + esc(x.note) + ')' : ''}</li>`).join('') + '</ul>';
    if (p.running_cards.length) h += `<div>It finishes card${p.running_cards.length > 1 ? 's' : ''} #${p.running_cards.join(', #')} first.</div>`;
    if (p.waiting_pinned_cards.length) h += `<div>${p.waiting_pinned_cards.length} approved card(s) only it can run will wait.</div>`;
    for (const w of p.warnings) h += `<div class="fl-warn">${esc(w)}</div>`;
    h += '<div class="fl-row" style="margin-top:8px">';
    h += `<button data-go="1">${verb} 1 h</button><button data-go="8">8 h</button><button data-go="0">Until I bring it back</button><button data-go="cancel">Cancel</button></div></div>`;
    return h;
  }

  async function act(machine, mode) {
    if (busy) return;
    if (mode === 'active') return send(machine, mode, {});
    try {
      const r = await fetch('/api/fleet/plan?machine=' + encodeURIComponent(machine));
      sheet = {machine, mode, plan: await r.json()};
    } catch (e) {
      sheet = {machine, mode, plan: {moves: [], uncovered: [], running_cards: [], waiting_pinned_cards: [], warnings: ['Could not check what this affects: ' + e.message]}};
    }
    render();
  }

  async function go(choice) {
    if (choice === 'cancel') { sheet = null; return render(); }
    const s = sheet;
    sheet = null;
    await send(s.machine, s.mode, {hours: Number(choice) || null, force: true});
  }

  async function send(machine, mode, extra) {
    busy = true;
    try {
      const r = await fetch('/api/fleet/mode', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({machine, mode}, extra))});
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.status);
      data = j.fleet;
    } catch (e) {
      alert('Fleet change failed: ' + e.message);
    }
    busy = false;
    render();
  }

  window.JarvisFleet = {load};
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && !sheet) load(); }, 60000);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => load()); else load();
})();
