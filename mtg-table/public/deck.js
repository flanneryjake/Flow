'use strict';
// Deck list parsing (Moxfield / Archidekt / MTGO text exports) and card lookup on Scryfall.
// Runs in the browser, so the server never needs internet access.

(function (global) {
  const LINE = /^\s*(\d+)\s*x?\s+(.+?)\s*$/i;

  function parseDeck(text) {
    const main = [], commander = [];
    let section = 'main';
    for (let raw of String(text).split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) continue;
      const header = line.replace(/^\/\/\s*/, '').replace(/:$/, '').toLowerCase();
      if (/^(commander|commanders)(\s*\(\d+\))?$/.test(header)) { section = 'commander'; continue; }
      if (/^(deck|main|mainboard|companion)(\s*\(\d+\))?$/.test(header)) { section = 'main'; continue; }
      if (/^(sideboard|maybeboard|considering|tokens)(\s*\(\d+\))?$/.test(header)) { section = 'skip'; continue; }
      if (section === 'skip') continue;
      let m = line.match(LINE);
      let qty = 1, rest = line;
      if (m) { qty = Number(m[1]); rest = m[2]; }
      let isCmdr = section === 'commander' || /\*CMDR\*|\[commander/i.test(rest);
      // Strip set codes, collector numbers, foil marks and Archidekt category tags.
      let name = rest
        .replace(/\[[^\]]*\]/g, '')
        .replace(/\^[^^]*\^/g, '')
        .replace(/\*[A-Z]+\*/g, '')
        .replace(/\s\([A-Za-z0-9]{2,6}\)(\s+[\w\-★]+)?\s*$/, '')
        .replace(/\s+#\S+$/, '')
        .trim();
      if (!name || /^(sideboard|deck|commander)$/i.test(name)) continue;
      (isCmdr ? commander : main).push({ qty, name });
    }
    return { main, commander };
  }

  function faceImages(card) {
    if (card.image_uris) {
      return { img: card.image_uris.normal, backImg: '', backName: '' };
    }
    if (card.card_faces && card.card_faces[0].image_uris) {
      return {
        img: card.card_faces[0].image_uris.normal,
        backImg: card.card_faces[1] && card.card_faces[1].image_uris ? card.card_faces[1].image_uris.normal : '',
        backName: card.card_faces[1] ? card.card_faces[1].name : '',
      };
    }
    return { img: '', backImg: '', backName: '' };
  }

  function toEntry(card, qty) {
    return { qty, name: card.name, type: card.type_line || '', ...faceImages(card) };
  }

  async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // Resolve names on Scryfall in batches of 75 (their collection endpoint limit).
  async function resolveDeck(parsed, onProgress) {
    const all = [...parsed.main.map(e => ({ ...e, zone: 'main' })), ...parsed.commander.map(e => ({ ...e, zone: 'commander' }))];
    const found = new Map(); // lowercased name -> card
    const missing = [];
    for (let i = 0; i < all.length; i += 75) {
      const batch = all.slice(i, i + 75);
      onProgress && onProgress(Math.min(i + 75, all.length), all.length);
      try {
        const res = await fetch('https://api.scryfall.com/cards/collection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ identifiers: batch.map(e => ({ name: e.name.split(' // ')[0] })) }),
        });
        if (!res.ok) throw new Error(`Scryfall ${res.status}`);
        const data = await res.json();
        for (const c of data.data || []) {
          found.set(c.name.toLowerCase(), c);
          found.set(c.name.split(' // ')[0].toLowerCase(), c);
        }
      } catch (e) {
        console.warn('Scryfall lookup failed, using text cards', e);
      }
      await sleep(120); // stay well under Scryfall's rate limit
    }
    const deck = { main: [], commander: [] };
    for (const e of all) {
      const c = found.get(e.name.toLowerCase()) || found.get(e.name.split(' // ')[0].toLowerCase());
      if (!c) missing.push(e.name);
      deck[e.zone].push(c ? toEntry(c, e.qty) : { qty: e.qty, name: e.name, type: '', img: '', backImg: '' });
    }
    return { deck, missing };
  }

  async function findToken(name) {
    const q = `t:token !"${name.replace(/"/g, '')}"`;
    try {
      const res = await fetch(`https://api.scryfall.com/cards/search?unique=art&q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } });
      if (res.ok) {
        const data = await res.json();
        if (data.data && data.data[0]) return toEntry(data.data[0], 1);
      }
    } catch { /* offline */ }
    return { name, type: 'Token', img: '', backImg: '' };
  }

  const api = { parseDeck, resolveDeck, findToken };
  if (typeof module !== 'undefined') module.exports = api; else global.Deck = api;
})(typeof window !== 'undefined' ? window : globalThis);
