---
name: printing
triggers: \b(print\w*|plates?|filament|flashforge|3d|nozzle|slic\w*|gcode|spool)\b
summary: 3D printing: only plates from a card Jake approved may auto-start; otherwise Jake taps Start; the camera bed check runs before every print, plates go in order, everything is pre-sliced.
---
- The 3D printer is a Flashforge on the apartment network, run by the print service on the 5060. Jake has no paper printer.
- A plate from a card Jake already approved may start on its own. Anything else waits for Jake to tap "Start plate N" in the phone app (PIN).
- The camera bed check must say the bed is clear before every print, either way.
- Plates go in order, and multi-plate jobs are sliced up front so he only taps Start.
- A filament watcher (load cell under the spool) is planned; it would pause only when Jake is away, and resuming is always his tap.
