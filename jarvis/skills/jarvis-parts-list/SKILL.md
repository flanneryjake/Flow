---
name: jarvis-parts-list
description: Add hardware Jake needs for a project to his running parts shopping list (cheapest listing, MUST HAVE / NEED / WANT, within budget) or check the list for sales.
---

# Jarvis parts shopping list

One running list of everything Jake needs to buy for future projects (Pi upgrades, Jarvis/Tars mics and speakers, MTG card scanner, Flipper, filament watcher...).

## Where it lives
- Master data: `jarvis-outputs/parts/parts.json` in the Apartment Jarvis project files (edit this; `build_sheet.py` rebuilds parts-list.xlsx).
- Jake's view: Google Sheet "Jarvis Parts to Buy" https://docs.google.com/spreadsheets/d/1E8gkoJiEyOa84rO6FbSWHTarKWlAPnk8ZS9rHvqG9lU/edit (Drive folder "Jarvis Outputs"). Without a Sheets connector it can't be edited in place: then give Jake the new rows, or update parts.json when you can reach it.

## Item format
```json
{"item": "...exact product, size/spec...", "project": "Raspberry Pi", "category": "MUST HAVE|NEED|WANT",
 "link": "cheapest listing URL", "store": "Amazon", "price": 8.99, "price_type": "estimate|checked",
 "checked": "YYYY-MM-DD", "status": "To buy|Hold|Bought|Skip", "notes": "why, compatibility gotchas",
 "baseline": null, "last_alert_price": null}
```

## Rules for adding
- Find the CHEAPEST current new listing for the exact item (US seller, include shipping when shown). Note compatibility gotchas (e.g. USB-C vs USB-A ports).
- Category: MUST HAVE only if the project cannot work without it; otherwise NEED or WANT. Default to NEED/WANT: Jake is paying down card debt.
- MUST HAVE total must stay under the budget: **$40/month**, taken out of his $150 Online shopping line (not on top of it). Review of that number: 2026-10-16.
- Check the list for duplicates and things he already owns before adding.
- Never buy anything: purchases are Jake's (PIN).

## Sale check
For each "To buy"/"Hold" item, re-check the cheapest listing, update price/link/store/checked, set price_type "checked", and set `baseline` the first time. Alert Jake only when the price is at least **10% AND $2** below baseline, and lower than `last_alert_price` (then update it). Otherwise stay quiet.
