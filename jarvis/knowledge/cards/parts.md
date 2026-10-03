---
name: parts
triggers: \b(parts?|hardware|shopping list|on sale|sale|cheapest|need to buy|must have|wish ?list)\b
summary: Parts list: one running list of hardware to buy, each MUST HAVE / NEED / WANT with the cheapest link; Jarvis never buys anything.
---
- Hardware for projects goes on one running parts list (a Google Sheet Claude keeps).
- Each part gets the cheapest current listing and a label: MUST HAVE only if the project can't work without it, otherwise NEED or WANT. Most things are NEED or WANT while Jake pays down his card.
- Jake hears about a part again only when it drops at least 10% and 2 dollars below its first checked price.
- Jarvis never buys or orders anything. To add a part, file it for Claude: "tell Claude to add X to the parts list".
