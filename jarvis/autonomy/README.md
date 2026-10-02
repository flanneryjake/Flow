# Jarvis autonomy lane

Lets Jarvis file its own cards and start the routine ones without a tap from Jake, while money, posting,
email, deleting and account changes still wait for the PIN.

Why: every card Jarvis creates today lands as staged (or, in Hub v3, "Confirm" when an AI moved it), and the
rig Worker holds cards as Tier C when a risky word appears anywhere in them. On 2026-09-30 the rig logged
"26 approved card(s) for rig, ran 0" for over a day; cards like "Smart Printer Watchdog" and "Automated Case
Study Generator" were held as "money/messaging/publish/accounts". Nothing in them spends, posts or deletes.

## Pieces

| File | What it does |
| --- | --- |
| `classify.py` | Gives a card a tier (`free`, `free_logged`, `ask_once`, `pin`, same names as `guardrails/policy.json`). Only the **title** (the card's objective) can hold a card; risky words in the body become "watch" notes. Negated or planning phrasing ("draft", "proposal", "don't publish", "before anything is bought") doesn't count. |
| `autotask.py` | Wraps `ghq.new_card`. Free / free_logged cards are filed as `status:approved` with an `auto` label, a comment naming the rule, and a wake; everything else is filed staged with the reason. |
| `test_*.py` | `python -m unittest` from this folder. The classifier cases are the real rig cards from the Tasks board. |

## Limits

- `JARVIS_AUTO_PER_DAY` (default 25): after that many auto cards in a UTC day, new cards are filed staged.
- `JARVIS_AUTO_DEPTH` (default 3): an auto card spawned from a chain of 3 auto cards is filed staged, so a
  loop of follow-ups can't run forever. A follow-up of a card Jake approved starts the count at 0.
- `JARVIS_AUTO_OFF=1`: kill switch, everything is filed staged.
- Duplicate titles (normalized) return the open card instead of filing a new one.

The card-level tier is only the first gate. The run itself must still deny send/post/delete/purchase tools
to unattended Claude runs and stop with `NEEDS PIN: <action>` (Rig Job Rules, Global Policy). That run-time
guard is what makes it safe to let routine cards start on their own.

## Wiring it in (agent.py and the hub, which live on the PCs, not in this repo)

1. Worker: where it creates follow-up cards, call `autotask.propose(title, body, machine=..., spawned_from=n)`
   instead of `ghq.new_card(...)` (or the Notion equivalent).
2. Worker: replace the keyword Tier C check with `classify(title, notes)[0] == 'pin'`, so a card is held only
   when its objective is a PIN action.
3. Hub v3: cards labelled `auto` skip the Confirm step; show the auto comment on the card so Jake can see why
   it ran, and give the kill switch a toggle on the System tab.

CLI: `python autotask.py check "Buy filament"` prints what would happen without filing anything.
