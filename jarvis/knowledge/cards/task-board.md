---
name: task-board
triggers: \b(cards?|board|approv\w*|snooz\w*|queue|tasks?|staged|needs[- ]jake|worker)\b
summary: Task board: GitHub cards in jarvis-tasks; approved cards run on their own, PIN cards (money, posting, deleting, accounts) always wait for Jake.
---
- Work lives as cards on the GitHub board flanneryjake/jarvis-tasks. Status goes inbox, staged (waiting for Jake), approved (will run), working, then closed when done. Snoozed means waiting on a file, another card, a machine or a time, not on Jake.
- An approved card runs by itself. Cards marked PIN (money, posting or sending, deleting personal data, accounts, exposing services) always wait for Jake's PIN.
- Jake approves from the phone app Inbox or the To-Do tab.
- When reporting cards, cite the card number and say which machine or Claude did what. Never invent card numbers.
