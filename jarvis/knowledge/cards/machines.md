---
name: machines
triggers: \b(rig|5060|homebase|home ?base|backup|junky|junk laptop|laptop|hal ?9000|raspberry|\bpi\b|frank|tailnet|tailscale|machines?|computers?|pcs?|fleet)\b
summary: Machines: TARS runs on the 5060 laptop (main homebase, always on), Jarvis on the rig (always on since 10/03), Junky POS is the backup (Home Assistant, search), Hal9000 is the Pi, Frank is a PC Jake is still building.
---
- The 5060 laptop (LAPTOP-4150EGRS) is the main homebase: phone app hub, a Worker, the print service, and TARS. TARS stays online there at all times.
- The rig (DESKTOP-VLLDDM4) is the big PC with 128 GB of RAM: the large Jarvis brain, the Docker sandbox, the main Worker and the "Hey Jarvis" mic. Since 10/03 it stays on all the time (idle shutdown is off). Heavy work goes here.
- Junky POS (the old junk laptop) is backup only: Home Assistant and the Echos, web search (SearXNG), DNS, the rig's wake relay, and low-priority idle services. Nobody runs a second hub or Worker there, and admin steps there get parked, never handed to Jake.
- Hal9000 is the Raspberry Pi (back burner). Frank is a PC Jake is still building.
- They all talk over Jake's Tailscale network (tailnet). Nothing is exposed to the public internet without Jake's PIN.
