---
description: Send a task to homebase to start right now (pauses whatever it is running) and show its progress
argument-hint: <what homebase should do>
---
Jake wants homebase to do this right now: $ARGUMENTS

1. Turn it into a short imperative title (under 80 characters) and a body with the concrete steps and a clear
   finish line. Keep Jake's own words in the body.
2. Run: `python C:\Jarvis\ghq\ghq.py now "<title>" --body "<body>"`
   It prints the card number and link. Tell Jake the link in one line.
3. Run `python C:\Jarvis\ghq\ghq.py watch <number>` in the background and relay each new progress block to Jake
   as it arrives, in one or two lines each. When it prints `Finished:`, give Jake the result.
4. If it finishes as `needs-jake`, show Jake the question homebase asked, word for word.

If the command says GITHUB_TASKS_TOKEN is not set, or the wake fails, the card still exists and homebase will
pick it up within a minute; say so rather than retrying.
