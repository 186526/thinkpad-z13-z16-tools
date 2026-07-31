# ThinkPad Z13 / Z16 Gen 2 Touchpad Tool

A small GUI + CLI utility for the **Sensel haptic touchpad** found in the
Lenovo ThinkPad Z13 Gen 2 / Z16 Gen 2:

- **Haptic intensity** slider (0-100, device default 50), applied live
- **Click force** slider (10-500 g, default 164 g) — the long-undocumented
  "click sensitivity", now controllable
- **Release threshold**, optionally auto-linked at 65% of the click force
- **Per-zone click / release forces** for the left, right and middle
  button zones (default 76 g / 50 g each)
- **Preset profiles** — light / standard / heavy click feel, one-click apply
- **Apply at login and after suspend** via two systemd user services
  (all settings are RAM-only on the device and reset after reboot/suspend)
- **HID feature viewer** — read-only view of every feature report the
  touchpad exposes over `/dev/hidraw*`
- **Device info page** — hidraw path, HID ID / name, driver and kernel module
- **Companion tools** — OLED brightness, IR/RGB camera detection, GPU-reset
  monitor, hardware probe, and a GNOME Quick Settings toggle (see below)

> Reference: [Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2 – Haptic Touchpad](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2#Haptic_Touchpad)

## How it works

The touchpad is an I2C-HID Sensel device (`2C2F:0027` / `2C2F:0028`).
Two interfaces are used:

1. **Haptic intensity** — HID feature `b0000` (*Haptic: Intensity*,
   report ID 11, range 0-100), written with a `HIDIOCSFEATURE` ioctl.
2. **Click force & friends** — firmware registers accessed through the
   **vendor HID pipe** (report ID 0x09, usage page `0xFF00` usage `0x0001`):
   a 3-byte read/write command `[cmd_hi, cmd_lo, size]` + data + checksum,
   21-byte frames, ACK-based (`1` = read OK, `5` = write OK). Register raw
   values are in **grams ÷ 2**.

All registers are **RAM-only** — they revert to defaults on reboot/suspend,
which is why the tool ships an apply-at-login service.

Verified facts (measured on a ThinkPad Z13 Gen 2):

- The touchpad **reports no force/pressure axis**. A click is generated
  entirely in device firmware: when the applied force crosses the register
  `0x0038` threshold, the firmware plays the haptic click and sets a single
  button bit in input report 3. So "click sensitivity" is a firmware-side
  force threshold, not something libinput can tune.
- The 256-byte vendor feature page `0xFF00:0xC5` is the Windows Precision
  Touchpad "Win8 compliance blob" (the kernel's `hid-multitouch` and the
  `hhd` project treat it exactly that way) — click sensitivity is **not**
  among those bytes; the Arch Wiki's negative result is explained by design.
- The haptic-intensity register `0x00AB` and feature report 11 are mirrored
  by the firmware: writing either one updates the other (verified by
  measurement on this device).

## Requirements

- Python 3.9+ (`haptic.py` / CLI need only the standard library)
- For the GUI: PyGObject (`python3-gi`), GTK4 and libadwaita
  (Debian: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`)
- Read/write access to `/dev/hidraw*` (e.g. member of the `plugdev` group,
  or udev uaccess on the active seat)

## Usage

```bash
# GUI
./z13-touchpad-tool

# CLI — haptic intensity (feature report path)
./z13-touchpad-apply --get                    # print current intensity
./z13-touchpad-apply --set 80                 # set intensity and save it

# CLI — firmware registers (click force etc.)
./z13-touchpad-apply --show                   # show all registers
./z13-touchpad-apply --set-click-force 100g   # click force in grams
./z13-touchpad-apply --set-click-release 65g  # release threshold in grams
./z13-touchpad-apply --set-haptic 60%         # haptic intensity via register
./z13-touchpad-apply --set-zone left 100 65   # left-zone click + release (g)
./z13-touchpad-apply --profile heavy          # apply a preset (light|standard|heavy)

# CLI — device & registers
./z13-touchpad-apply --list-devices           # list all /dev/hidraw* with HID_NAME
./z13-touchpad-apply --reg 0x0038 read        # read one firmware register
./z13-touchpad-apply --reg 0x00AB 70          # write one register (grams/% for known ones)
./z13-touchpad-apply --watch-test             # run the resume-watcher self-test once

# apply the saved config (used by the login service)
./z13-touchpad-apply

# HID feature dump
./feature-probe.py --dump
```

In the GUI:

- **点击力度** — drag to set the click force (10-500 g); the release
  threshold follows at 65% while the link switch is on.
- **力度预设** — pick 轻触 / 标准 / 重按 to apply a click+release preset.
- **左区/右区/中区** — one click and one release slider per button zone.
- **触感强度** — drag to adjust haptic feedback (0-100, default 50).
- "恢复默认" resets haptic intensity to 50.
- Turn on "登录后自动应用" to install & enable `z13-touchpad-haptic.service`
  (re-apply at login) and `z13-touchpad-resume.service` (re-apply after
  suspend, via a `PrepareForSleep` D-Bus watcher) in
  `~/.config/systemd/user/`.
- The "HID 功能" page shows every feature report plus a read-only view of
  all firmware registers; the "设备" page shows the hidraw path, HID ID /
  name, driver and kernel module of the detected touchpad.

Configuration is stored in `~/.config/z13-g2-tools/config.json`.

## Companion tools

- **OLED brightness** — `./z13-brightness-apply --get|--set N|--to-windows N|--to-linux N`
  maps between Linux and Windows brightness scales and writes the kernel
  value; permission notes in [docs/brightness-permissions.md](docs/brightness-permissions.md).
  On wake from suspend the resume watcher re-applies the reported brightness.
- **Camera detection** — `./z13-camera-tool` labels `/dev/video*` as RGB or
  IR (IR = `04f2:b78c`), so web apps can be steered to the RGB camera; see
  its `--help` for PipeWire and v4l2 guidance.
- **GPU-reset monitor** — `./gpu-reset-watch.py` watches the kernel log for
  amdgpu resets and logs them (with a desktop notification if available).
- **Hardware probe** — `./hw-probe.py` checks fingerprint reader, TPM and
  cameras and writes [docs/hardware-findings.md](docs/hardware-findings.md).
- **Blob sweep** — `./blob-sweep.py` is an *experimental* single-byte
  explorer for feature report 7 (the 256-byte Win8 PTP blob), with snapshot
  and auto-restore. **Dangerous: only run from a graphical session while
  testing the touchpad by hand.**
- **GNOME extension** — `extensions/z13-touchpad-quick@user` adds a
  haptic-intensity on/off toggle to the Quick Settings panel; install with
  `cd extensions && ./install.sh` (see its README).

## Firmware registers

| Register | Name | Default | Meaning |
|---|---|---|---|
| `0x0038` | Click force | 164 g | force to trigger a click (the "click sensitivity") |
| `0x0090` | Click release | 108 g | force to release the click (hysteresis) |
| `0x0091`-`0x0092` | Left zone click / release | 76 g / 50 g | left-button zone |
| `0x0093`-`0x0094` | Right zone click / release | 76 g / 50 g | right-button zone |
| `0x0095`-`0x0096` | Middle zone click / release | 76 g / 50 g | middle-button zone |
| `0x00AB` | Haptic intensity | 50 % | mirrors feature report 11 |
| `0x006E` | Haptics enabled | ON | master haptic switch |

Raw values are grams ÷ 2. All writes are RAM-only.

## HID feature reports

| Report | Size | Usage | Meaning | Default |
|---|---|---|---|---|
| 3  | 1 B  | Vendor 0xFF00:0x01 | vendor parameter | `0x01` |
| 4  | 1 B  | Digitizers Inputmode | input mode (0-10) | `3` |
| 6  | 2 bit | Surface Switch / Button Switch | surface & button toggles | `0` |
| 7  | 256 B| Vendor 0xFF00:0xC5 | Win8 PTP compliance blob | all `0x00` |
| 8  | 8 bit | Contact Max / Button Type | 4 bits each | Contact Max `5` |
| 10 | 1 bit | Digitizer Vendor 0x60 | vendor flag | `0` |
| 11 | 1 B  | Haptic: Intensity | haptic feedback strength (0-100) | `50` |
| 12 | 1 B  | Vendor 0xFF00:0x01 | vendor parameter | `0x00` |

## Credits / Research

The click-force register interface was reverse-engineered and published by
others; this project verified their findings on a Z13 Gen 2 and wraps them
in a friendly GUI:

- [glutamatt/sensel-touchpad-linux](https://github.com/glutamatt/sensel-touchpad-linux)
  — Linux tool, register map and the report-0x09 pipe protocol
- [daniel-bavrin/cirque-fix](https://github.com/daniel-bavrin/cirque-fix)
  — independent Windows-side implementation (decompiled
  `SenselSerialDevice.dll` from the vendor's "Cirque Touchpad Custom
  Settings" app), identical registers and framing

Their results agree byte-for-byte, and this project's read-back on the
Z13 Gen 2 matches the documented defaults exactly.

## Files

| File | Purpose |
|---|---|
| `haptic.py` | device detection + feature-report ioctls + register pipe (stdlib only) |
| `gui.py` | GTK4/libadwaita GUI (settings + presets + zones + device page) |
| `z13-touchpad-apply` | CLI: apply config / `--get` / `--set` / `--show` / `--set-click-*` / `--set-zone` / `--profile` / `--reg` / `--list-devices` / `--watch-test` |
| `z13-touchpad-tool` | GUI launcher |
| `feature-probe.py` | experimental single-byte writer for the feature-report bank |
| `resume-watch.py` | D-Bus `PrepareForSleep` watcher; re-applies settings + brightness after suspend |
| `brightness.py` | OLED backlight detection + read/write + Linux↔Windows scale (stdlib only) |
| `z13-brightness-apply` | CLI for brightness (get/set/convert) |
| `z13-camera-tool` | IR/RGB camera detection + app-level switching guidance |
| `gpu-reset-watch.py` | kernel-log watcher that logs amdgpu resets |
| `hw-probe.py` | fingerprint / TPM / camera probe → `docs/hardware-findings.md` |
| `blob-sweep.py` | experimental report-7 byte sweeper with snapshot/restore |
| `extensions/` | GNOME Quick Settings haptic toggle (see its README) |
| `docs/` | permission notes + hardware findings |

## License

MIT — see [LICENSE](LICENSE).
