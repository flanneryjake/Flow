---
name: jarvis-3d-printing
description: Jake's 3D printing rules and setup (Flashforge on the home network, print service on the 5060): Jake-approved cards may auto-start, camera bed check every print, plates in order, everything pre-sliced, filament watching.
---

# 3D printing

## Setup
- Printer: Flashforge (Adventurer 5M Pro per earlier notes) at 172.16.14.10 on the apartment network. Its camera stream (port 8080) has been off; turning it on needs the printer's touchscreen or code.
- Print service runs on homebase (the 5060): :8795, files in C:\Jarvis\printer (print_service.py, plates.json, bed_check.py). The phone app (https://laptop-4150egrs.tail3bbcb8.ts.net) has a Printer card with "Start plate N" (PIN-gated).
- Jake has NO paper printer.

## Rules (Jake, 2026-09-30; rule 1 changed 2026-10-03)
1. **Starting a plate.** A print from a card Jake already approved may auto-start without the approval code/PIN (Jake said yes on card #142, 2026-10-03). Anything else waits for Jake to tap Start in the app. The camera bed check still runs before EVERY print, auto-started or not.
2. Before a plate is offered, the service checks: printer Ready (BUILDING_COMPLETED means the last plate is still on the bed), plate N is the next one after the last reported finish, and the camera bed check says the bed is clear (unsure -> photo + PIN override; camera off -> blind PIN confirm).
3. Multi-plate jobs: slice EVERY plate up front so he only taps Start. Flag any plate whose gcode is incomplete.
4. Keep plates in order; record which plate finished last.
5. Changes to the print service follow these rules; back up files before editing.

## Filament watching (planned, Flow draft PR #32)
- Blink Mini camera is cloud-only (snapshots every 5-10 min at best): fine for an alert photo, not for judging if a plate will finish.
- Plan: ESP32 + HX711 + 5 kg load cell under the spool for exact grams; compare grams left vs grams the job still needs; pause only when Jake is away (HA presence person.jake_flannery). Resume is always Jake's tap.
