---
name: printing
triggers: \b(print\w*|plates?|filament|flashforge|3d|nozzle|slic\w*|gcode|spool)\b
summary: 3D printing: plates never auto-start; Jake taps Start in the app after the bed check, plates go in order, everything is pre-sliced.
---
- The 3D printer is a Flashforge on the apartment network, run by the print service on the 5060. Jake has no paper printer.
- Plates NEVER start on their own. Jake taps "Start plate N" in the phone app (PIN) once the camera bed check says the bed is clear.
- Plates go in order, and multi-plate jobs are sliced up front so he only taps Start.
- A filament watcher (load cell under the spool) is planned; it would pause only when Jake is away, and resuming is always his tap.
