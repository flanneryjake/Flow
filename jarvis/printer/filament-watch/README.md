# Filament watch

Pauses the Adventurer 5M Pro when the spool doesn't have enough filament left to finish the job and Jake isn't home,
then sends his phone an alert with a photo of the spool from the Blink Mini. Resume is always Jake's tap on the
printer or in the hub. When he's home it only warns.

The printer's own runout sensor still covers "filament fully ran out" (Settings on the touchscreen, filament detection
on). This adds the earlier warning: "this spool won't make it to the end of this plate".

| Piece | Where | What it does |
| --- | --- | --- |
| `spool-scale.yaml` | flashed to an ESP32 (ESPHome) | Weighs the spool: HX711 + 5 kg bar load cell under the spool holder, `sensor.spool_scale_weight` |
| `packages/filament_watch.yaml` | HA config `packages/` on the junk laptop | Reads the printer over LAN, works out grams left vs grams the job still needs, pauses + alerts |

Why a scale and not the camera: the Blink Mini has no local stream, so every frame comes through Amazon's cloud,
at best every few minutes, and the HA Blink login has to be redone now and then. A photo is fine for the alert,
but it can't tell "250 g left" from "150 g left". The scale can.

## How it decides

- Printer `/detail` (port 8898, polled every 60 s): `status`, `printProgress` (0-1), `estimatedRightWeight`
  (grams for the whole job).
- `Filament job still needs` = job grams x (1 - progress).
- `Filament left` = scale reading - `Empty spool weight` (default 230 g, change it per brand in HA).
- Short = printing and left < needs + `Filament safety margin` (default 25 g), held for 2 minutes so a tug on the
  spool doesn't trigger it.
- Short and `person.jake_flannery` is anything but `home` (away, or unknown) -> pause + alert. Short and home -> alert only.
- `input_boolean.filament_watch` turns the whole thing off.

## Setup

1. **Wire the scale.** Load cell red/black/white/green to HX711 E+/E-/A-/A+. HX711 VCC to 3V3, GND to GND,
   DT to GPIO16, SCK to GPIO4. The load cell sits under the spool holder (printed base, one end bolted down,
   the other end carries the holder).
2. **Flash it** from any PC with the ESP32 on USB: `pip install esphome`, put `wifi_ssid`, `wifi_password`
   and `api_key` in a `secrets.yaml` beside `spool-scale.yaml`, then `esphome run spool-scale.yaml`.
3. **Calibrate.** In the ESPHome log, note the raw value with the empty holder, then with something of known
   weight (a full 1 kg spool still sealed, or a kitchen-scale-weighed item). Put both numbers in the
   `calibrate_linear` lines and run `esphome run` again (it updates over Wi-Fi now).
4. **Add it to HA:** Settings > Devices > ESPHome should discover `spool-scale`; use the `api_key`.
5. **Blink:** in the Blink app name the camera `Spool` and point it at the spool. In HA add the Blink integration
   (Settings > Devices > Add > Blink) so `camera.spool` shows up. Create the folder `<HA config>/www/filament/`.
6. **Printer login:** the HTTP API needs the printer's serial number and check code. Turn on LAN mode on the printer
   (Settings > network) to see the check code; the serial is on the About page. Then in HA `secrets.yaml`:

   ```yaml
   ff_detail_url: http://172.16.14.10:8898/detail
   ff_control_url: http://172.16.14.10:8898/control
   ff_detail_body: '{"serialNumber":"SNxxxx","checkCode":"xxxxxxxx"}'
   ff_pause_body: '{"serialNumber":"SNxxxx","checkCode":"xxxxxxxx","payload":{"cmd":"jobCtl_cmd","args":{"jobID":"","action":"pause"}}}'
   ```

7. **Presence:** Home Assistant app on Jake's phone, location "Always", and the phone attached to `person.jake_flannery`
   (Settings > People; it is `person.jake_flannery`, tracker `device_tracker.iphone`).
8. Copy `packages/filament_watch.yaml` into `<HA config>/packages/`, check the config, restart HA.

## Test without wasting filament

Start any print, then set `Empty spool weight` to a number above the scale reading (so `Filament left` drops to 0).
Within about 3 minutes: home -> heads-up only; set `person.jake_flannery` away (or turn the phone's location off) -> the
printer pauses and the alert arrives. Put the empty weight back and resume on the printer.

This doesn't touch `C:\Jarvis\printer` (the print service); it only sends the printer's own pause command.
API reference: github.com/Parallel-7/flashforge-api-docs (`endpoints_5m_3.2.7.yaml`).
