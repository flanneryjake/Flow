'use strict';
const test = require('node:test');
const assert = require('node:assert');
const game = require('../game');
const { parseDeck } = require('../public/deck');

function setup() {
  const room = game.newRoom({ format: 'commander' });
  const a = game.join(room, 'Jake');
  const b = game.join(room, 'Friend');
  const deck = {
    main: [
      { qty: 30, name: 'Island', type: 'Basic Land', img: 'https://cards.scryfall.io/normal/front/i.jpg' },
      { qty: 1, name: 'Sol Ring', type: 'Artifact', img: 'https://evil.example.com/x.jpg' },
    ],
    commander: [{ qty: 1, name: 'Urza, Lord High Artificer', type: 'Legendary Creature' }],
  };
  game.apply(room, a.pid, { type: 'deck', deck });
  return { room, a, b };
}

test('deck load, life and zones', () => {
  const { room, a } = setup();
  assert.equal(a.zones.library.length, 31);
  assert.equal(a.zones.command.length, 1);
  assert.equal(a.life, 40);
  const sol = Object.values(room.db).find(c => c.name === 'Sol Ring');
  assert.equal(sol.img, '', 'non-Scryfall image URLs are dropped');
});

test('other players never see your hand or library', () => {
  const { room, a, b } = setup();
  game.apply(room, a.pid, { type: 'draw', n: 7 });
  const theirs = game.view(room, b.pid).players.find(p => p.pid === a.pid);
  assert.equal(theirs.zones.hand.length, 7);
  assert.ok(theirs.zones.hand.every(c => c.hidden && !c.cardId));
  assert.deepEqual(theirs.zones.library, { count: 24 });
  const mine = game.view(room, a.pid).players.find(p => p.pid === a.pid);
  assert.ok(mine.zones.hand.every(c => c.cardId));
  // Commander is public.
  assert.ok(theirs.zones.command[0].cardId);
});

test('face-down cards are hidden from others but not the owner', () => {
  const { room, a, b } = setup();
  game.apply(room, a.pid, { type: 'draw', n: 1 });
  const iid = a.zones.hand[0];
  game.apply(room, a.pid, { type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.8, faceDown: true });
  assert.equal(game.view(room, b.pid).battlefield[0].cardId, null);
  assert.ok(game.view(room, a.pid).battlefield[0].cardId);
  assert.ok(!room.log.at(-1).text.includes('Island'), 'log does not leak a face-down card');
});

test('you cannot take cards from someone else\'s hand', () => {
  const { room, a, b } = setup();
  game.apply(room, a.pid, { type: 'draw', n: 1 });
  assert.throws(() => game.apply(room, b.pid, { type: 'move', iid: a.zones.hand[0], to: 'battlefield', x: 0, y: 0 }));
});

test('cards return to their owner, tokens vanish, tap and counters', () => {
  const { room, a, b } = setup();
  game.apply(room, a.pid, { type: 'draw', n: 1 });
  const iid = a.zones.hand[0];
  game.apply(room, a.pid, { type: 'move', iid, to: 'battlefield', x: 0.5, y: 0.8 });
  game.apply(room, a.pid, { type: 'control', iid, pid: b.pid });
  game.apply(room, b.pid, { type: 'tap', iid });
  game.apply(room, b.pid, { type: 'counter', iid, kind: '+1/+1', delta: 2 });
  const bf = room.battlefield.find(x => x.iid === iid);
  assert.equal(bf.tapped, true);
  assert.equal(bf.counters['+1/+1'], 2);
  game.apply(room, b.pid, { type: 'move', iid, to: 'graveyard' });
  assert.ok(a.zones.graveyard.includes(iid), 'goes to the owner\'s graveyard');
  game.apply(room, b.pid, { type: 'token', card: { name: 'Treasure' } });
  const tok = room.battlefield.at(-1).iid;
  game.apply(room, b.pid, { type: 'move', iid: tok, to: 'graveyard' });
  assert.ok(!b.zones.graveyard.includes(tok));
  assert.ok(!room.cards[tok]);
});

test('mulligan, peek and table limit', () => {
  const { room, a } = setup();
  game.apply(room, a.pid, { type: 'mulligan' });
  assert.equal(a.zones.hand.length, 7);
  assert.equal(game.peekLibrary(room, a.pid, 3).length, 3);
  game.join(room, 'C'); game.join(room, 'D');
  assert.throws(() => game.join(room, 'E'), /full/);
  // Rejoining with a token gives the same seat.
  assert.equal(game.join(room, 'Jake again', a.token).pid, a.pid);
});

test('deck parser handles Moxfield and Archidekt exports', () => {
  const d = parseDeck(`Commander
1 Urza, Lord High Artificer (MH1) 75

Deck
1 Sol Ring (C21) 263 *F*
4x Island
1x Myr Retriever (5DN) 140 [Creature]
1 Delver of Secrets // Insectile Aberration

Sideboard
1 Pithing Needle`);
  assert.deepEqual(d.commander, [{ qty: 1, name: 'Urza, Lord High Artificer' }]);
  assert.deepEqual(d.main.map(c => [c.qty, c.name]), [[1, 'Sol Ring'], [4, 'Island'], [1, 'Myr Retriever'], [1, 'Delver of Secrets // Insectile Aberration']]);
  const tagged = parseDeck('1x Atraxa, Praetors\' Voice (2X2) 190 [Commander{top}]\n1 Sol Ring');
  assert.equal(tagged.commander[0].name, 'Atraxa, Praetors\' Voice');
});
