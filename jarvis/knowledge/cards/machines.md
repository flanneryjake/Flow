---
name: machines
triggers: \b(rig|5060|homebase|home ?base|backup|junky|junk laptop|laptop|hal ?9000|raspberry|\bpi\b|frank|tailnet|tailscale|machines?|computers?|pcs?|fleet)\b
summary: Machines: Tars runs on the 5060 laptop (main homebase), Jarvis on the rig, Junky POS is the backup (Home Assistant, search), Hal9000 is the Pi, Frank is a PC Jake is still building.
---
- The 5060 laptop (LAPTOP-4150EGRS) is the main homebase: phone app hub, the Worker, the print service, and Tars (this chat model).
- The rig (DESKTOP-VLLDDM4) is the big PC: it runs the large Jarvis model, the Docker sandbox and the "Hey Jarvis" mic. It sleeps after 10 idle minutes and is woken only for about an hour of queued work, a Wake tap or an urgent card.
- Junky POS (the old junk laptop) is backup only: Home Assistant and the Echos, web search (SearXNG), DNS, and the rig's wake relay. Nobody restarts its Worker.
- Hal9000 is the Raspberry Pi (being set up the weekend of October 3). Frank is a PC Jake is still building.
- They all talk over Jake's Tailscale network. Claude can work on the PCs through Remote Control; Jake only taps Yes on admin prompts.
