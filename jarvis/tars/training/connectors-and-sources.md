# Connectors and sources for Jarvis

This doc covers what would make Jarvis genuinely useful to talk to, ranked by value for the effort and best first. Free options come first. Sources checked on 2026-10-02 are marked (checked). Anything I couldn't confirm from here is marked (unverified).

## Already built (on Tars today)

| What | How it reaches Jarvis | Lane |
|---|---|---|
| Web search: SearXNG on the backup laptop, on the tailnet (`100.90.201.22:8888`, JSON) | `LOOKUP:` → `lookup.searx()`, which reads the top two pages | lookup |
| Weather: National Weather Service (api.weather.gov, free, no key) | LIVE block when the question mentions weather words | live |
| Sunrise and sunset, computed locally (NOAA equations, no network) | LIVE block | live |
| Home Assistant snapshot (alarms, what's playing, listening switch, lights) and Echo voice commands | snapshot → LIVE block; commands run in code before the model | home |
| Gemini (gemini-3.5-flash, flash-lite) | fallback when SearXNG returns nothing and the topic isn't private | lookup |
| Claude (`claude -p` with WebSearch) | `ASK_CLAUDE:`, "ask Claude …", private topics, last resort | claude |
| GitHub task board (FACTS block), card filing ("tell Claude to …", "research …") | in code | tasks, card |
| Rolling conversation summary (`summary.json`) | "Memory of earlier conversations" line | all |

What this training set adds: the 213 prompts and 257 conversations in this folder teach the model the shapes above. It learns to answer from LIVE, FACTS and NOTE, to route with one line, and to say what a result does and doesn't show.

## Ranked: what to add next

**1. Feed `jake-facts.md` every turn.** Free. All lanes. Effort: about 15 minutes.
- What it fixes: "what do I usually do on Mondays" and "where should I shop". It also stops the model guessing which stores, machines or days Jake means.
- How: add the file as one more `[context]` line in `chat()`, or put it in the Modelfile SYSTEM prompt.
- Keep it under about 300 tokens, because `num_ctx` is 8192 and the FACTS block can be large.

**2. Read-only Google Calendar.** Free (Google Calendar API, OAuth). New LIVE line. Effort: an evening.
- Example prompts: "what's on today", "am I free Sunday afternoon".
- This is the biggest gap in the training data. Several replies currently have to say "your calendar isn't connected to me yet".
- How: run it on homebase or Tars and push today's and tomorrow's events into a LIVE line, the same way HA pushes `/ha`.
- Read-only scope means nothing can be changed by voice.

**3. Sports scores as LIVE data.** Free. Turns many `lookup` questions into `live`. Effort: about 2 hours.
- Example prompts: "did the Pats win", "Celtics score", "when do the Bruins play".
- ESPN's scoreboard JSON (`site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard`) needs no key (unverified, unofficial, could change). The NHL also publishes a public API (unverified).
- Why: search snippets for scores are often previews rather than results. That is the most common "couldn't confirm" case in the data.

**4. Commute and alert data for the 4 AM briefing.** Free. LIVE block. Effort: about 2 hours.
- Example prompts: "is the commuter rail on time", "any weather alerts".
- MBTA V3 API alerts for the Kingston Line: free, and a key raises the rate limit (unverified).
- NWS active alerts: `api.weather.gov/alerts/active?point=41.9584,-70.6673` uses the same keyless API that's already in use (unverified from here; the proxy blocked the test).

**5. Tavily as the second search source, ahead of Gemini.** Free tier: 1,000 credits a month, and a basic search costs 1 credit (checked). Lane: lookup. Effort: about an hour.
- Why: Google's pricing page lists "Grounding with Google Search" as *not available on the free tier* for the Flash models (checked). So the Gemini fallback answers from memory rather than searching, which is weak for current facts. The 9/29 429s were a quota limit on top of that.
- Proposed order: SearXNG, then Tavily, then Claude. Keep Gemini only for "ask Gemini …".

**6. News headlines from RSS.** Free. LIVE block for "news" questions. Effort: about 2 hours.
- Example prompts: "what's in the news", "anything happen in Plymouth".
- Possible feeds: NPR and WBUR, AP top news through a feed reader or RSS bridge, and a Plymouth local feed (feed URLs unverified).
- Cache them every 30 minutes and give the model 5 titles with dates. That is faster and more reliable than searching for "news today".

**7. Better conversation memory.** Free. All lanes. Effort: an evening.
- Example prompts: "what did I say about the dentist", "same as last time".
- The rolling summary exists already. Add a small `remember: …` store that Jake confirms by voice, such as "remember my landlord's name is …", and put it into context next to `jake-facts.md`.
- Don't log anything work-related into memory.

**8. Offline Wikipedia with Kiwix (kiwix-serve on the rig or homebase).** Free. Lane: lookup, as a source before the web. Effort: 1 to 2 hours plus the download.
- Example prompts: "how tall is Mount Washington", "who was Miles Davis", including when the internet is down.
- The full English Wikipedia ZIM without pictures is tens of GB (unverified size). kiwix-serve exposes a search URL that `lookup.py` could query like SearXNG.

**9. Gmail summaries (read-only).** Free API. LIVE line or ASK_CLAUDE. Effort: an evening.
- Example prompts: "anything important in my email", "did the package ship".
- Value is real but lower, and privacy risk is higher. Only pass sender and subject lines, and exclude anything from work.
- An alternative is to let `claude -p` use a Gmail connector on Jake's login, so no token sits on Tars (unverified for the CLI).

**10. Spotify Web API.** Lane: home. Effort: an evening, for little gain.
- Spotify's February 2026 changes (checked) mean Development Mode apps need the owner to have Premium, new apps are limited to 5 users, and several browse and batch endpoints were removed.
- Playback through Alexa and "what's playing" through HA already work. Skip this unless Jake wants things like "add this to my playlist".

**11. More of the apartment in Home Assistant.** Free. Lane: home. Effort varies.
- Example prompts: "is the print done", "what percent is the print at".
- The printer's own integration (OctoPrint, Bambu, Moonraker, depending on the printer) would put progress and ETA into the snapshot. Today only its smart plug is visible.
- Echo reminders aren't exposed to HA. That's why the training data sends "remind me …" to the Echo by voice.

## Fine-tune (LoRA) or prompt plus examples?

Start with prompt, examples and context. Fine-tune later, once there is more reviewed data.

- **On the hardware:** Unsloth's Qwen3.5 guide says a bf16 LoRA of the 9B needs about 22 GB of VRAM, and it advises *against* 4-bit QLoRA for Qwen3.5 (checked). The 5060's 8 GB can't do it. Whether the rig can depends on its GPU, and I haven't confirmed that. Frank, or a rented cloud GPU for an hour, would work. Unsloth exports GGUF for Ollama (checked). Use the same chat template at inference as in training; Unsloth calls a mismatched template the most common cause of a bad export (checked).
- **On the data:** 257 conversations are enough to shift tone and routing, but they could also teach a 9B to parrot these exact Plymouth facts and dates. Aim for 600 to 1,000 reviewed examples first. Grow the set from real `history.jsonl` turns that Jake marks as good, run `check_training.py` on every batch, and train only on assistant turns.
- **Do now:** put `modelfile_examples.txt` into the Modelfile, add `jake-facts.md`, and do items 2 to 5 above. Most "can't have a conversation" moments come from missing data (calendar, scores, alerts), not missing style. Once those are in place, fine-tune if the voice still drifts.

## Sources checked
- Tavily credits: https://docs.tavily.com/documentation/api-credits
- Gemini pricing and grounding: https://ai.google.dev/gemini-api/docs/pricing
- Spotify Feb 2026 dev mode changes: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- Unsloth Qwen3.5 fine-tuning: https://unsloth.ai/docs/models/qwen3.5/fine-tune
