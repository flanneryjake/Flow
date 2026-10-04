# MTG Table

A private web table for playing Magic: The Gathering with friends. Only people with a link can play:

- **Host key** (`MTG_HOST_KEY`): needed to open the lobby and create tables. Jake keeps it.
- **Room links** (`/r/<32 hex characters>`): anyone with the link can sit down (up to 4 players). The ids can't be guessed.

## What works now (table mode)

- Rooms for 1v1 (20 life) or Commander (40 life), up to 4 players, seats kept across reloads.
- Paste a deck list from Moxfield, Archidekt or MTGO. Cards are looked up on Scryfall in the browser and shown with real images. A "Commander" heading puts cards in the command zone.
- Drag cards between hand, battlefield, graveyard, exile, command zone and library (top or bottom). Double-click to tap, right-click for everything else: counters, face down, transform, token copies, give control.
- Draw, draw 7, mulligan, shuffle, look at the top X, search library, mill, tokens (looked up on Scryfall), dice, coin, life, poison and commander damage, chat and a game log.
- The server is authoritative and hides what you shouldn't see: other players' hands and libraries, and face-down cards.
- In a 2-player game each player sees their own side at the bottom.

There are no rules in table mode, the same as a real table or Tabletop Simulator.

## Run it

Needs Node 18+.

```
cd mtg-table
npm install
MTG_HOST_KEY=<long random string> npm start      # PowerShell: $env:MTG_HOST_KEY="..."; npm start
```

Open `http://<pc>:8795/?key=<host key>`, create a table and send friends the room link.
Settings: `PORT` (8795), `HOST` (0.0.0.0), `MTG_DATA_DIR` (./data, where rooms are saved).

Sharing outside the tailnet (Tailscale Funnel) makes it reachable from the internet. That needs Jake's approval first.

## Tests

```
npm test
```

## Roadmap

1. **Rules mode:** a real stack and rules enforcement using [XMage](https://github.com/magefree/mage) (MIT, 32,000+ cards) as a headless engine, with this web page as the client. The approach follows [mtg-colosseo's XMage bridge](https://github.com/glicerico/mtg-colosseo/pull/1), which turns every rules decision into JSON over WebSocket. XMage is a Java server, so it runs on the rig, not the 5060.
2. **Play against Claude:** an AI seat that receives the legal moves from the bridge and picks one.
