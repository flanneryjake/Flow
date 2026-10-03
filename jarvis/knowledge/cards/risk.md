---
name: risk
triggers: \b(docker|sandbox|risk\w*|safe to|install\w*|pin|permission|guardrails?)\b
summary: Risk: medium or high risk code runs in the rig's Docker sandbox first; money, posting, deleting, accounts and exposing services always need Jake's PIN.
---
- Risky code is tested in the rig's Docker sandbox before it is installed. A medium-risk change that passes cleanly goes ahead without a PIN and its card is titled "[Docker-tested]". High risk still goes to Jake after the test.
- Always Jake's PIN: spending money, posting or sending outside, deleting personal data, accounts and passwords, exposing a service to the internet, merging to main.
- Clinical or legal text never goes to Gemini. No secrets or patient details anywhere.
