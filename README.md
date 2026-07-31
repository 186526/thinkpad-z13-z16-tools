# ThinkPad Z13 / Z16 Gen 2 All-in-one Toolkit

An **all-in-one toolkit managed from a single GUI** for the Lenovo ThinkPad
Z13 Gen 2 / Z16 Gen 2: haptic touchpad (GUI + CLI), OLED brightness, IR/RGB
camera detection, GPU-reset monitoring, hardware probing and a GNOME Quick
Settings extension. The Sensel haptic touchpad control is the core; the rest
are companion tools that complete a daily-driver toolkit.

## Overview

### Touchpad (core)

- **Haptic intensity** slider — 0-100, default 50, **snapping to the five
  Windows-driver steps** (0 / 25 / 50 / 75 / 100); the device register
  `0x00AB` and feature report 11 mirror each other
- **Click force / release threshold** — 10-500 g, defaults 164 g / 108 g,
  optionally linked (release follows 65% of the click force)
- **Preset profiles** — light / standard / heavy, one-click apply to the
  main-zone click + release
- **Top-button presets** — set click + release for all three top-edge
  buttons (left / middle / right) at once
- **Per-zone forces** — one click / release slider per button zone,
  selected by clicking a visual touchpad map
- **Asynchronous writes + bottom progress bar** — device writes run on a
  background thread, the UI never blocks
- **One-click factory reset** — writes all registers back to firmware
  defaults
- **Auto-restore** — re-applies everything at login and after suspend
  (all settings are RAM-only and reset on reboot/suspend)
- **HID feature viewer / device info page** (developer mode) — read-only
  view of every feature report, firmware register and device detail

### Companion tools

- **OLED brightness** — GUI slider + CLI, with Linux↔Windows scale conversion
- **Camera detection** — labels `/dev/video*` as IR / RGB and gives
  app-level switching guidance
- **GPU-reset monitor** — watches the kernel log for amdgpu resets,
  logs them and notifies
- **Hardware probe** — fingerprint / TPM / camera probe, writes
  `docs/hardware-findings.md`
- **GNOME Quick Settings extension** — one-click haptic-intensity toggle in
  the Quick Settings panel (calls the `z13-touchpad-apply` CLI, no HID
  protocol re-implemented in JS)

> Reference: [Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2 – Haptic Touchpad](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2#Haptic_Touchpad)

## Quick start

### Requirements

- Python 3.9+ (`haptic.py` / the CLIs need only the standard library)
- For the GUI: PyGObject (`python3-gi`), GTK4 and libadwaita
  (Debian: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`)
- Read/write access to `/dev/hidraw*` (e.g. member of the `plugdev` group,
  or udev uaccess on the active seat)

### Launch the GUI

```bash
./z13-touchpad-tool            # normal mode
./z13-touchpad-tool --developer  # developer mode (adds HID / device pages)
```

### CLI examples

```bash
# Touchpad — haptic intensity (feature-report path)
./z13-touchpad-apply --get                    # print current intensity (0-100)
./z13-touchpad-apply --set 80                 # set intensity and save it

# Touchpad — firmware registers (click force etc.)
./z13-touchpad-apply --show                   # show all registers
./z13-touchpad-apply --set-click-force 100g   # click force in grams
./z13-touchpad-apply --set-click-release 65g  # release threshold in grams
./z13-touchpad-apply --set-haptic 60%         # haptic intensity via the register pipe
./z13-touchpad-apply --set-zone left 100 65   # left-zone click+release (g; omit release → 65% of click)
./z13-touchpad-apply --profile heavy          # apply a preset (light|standard|heavy)

# Touchpad — device & registers
./z13-touchpad-apply --list-devices           # list all /dev/hidraw* with HID_NAME
./z13-touchpad-apply --reg 0x0038 read        # read one firmware register
./z13-touchpad-apply --reg 0x00AB 70          # write one register (grams/percent auto-converted for known ones)
./z13-touchpad-apply --watch-test             # run the resume-watcher self-test once (re-applies the config)

# Apply the saved config (what the login service runs); this is also the
# no-argument behaviour
./z13-touchpad-apply

# Brightness / camera
./z13-brightness-apply --get                  # current brightness
./z13-brightness-apply --set 50               # set brightness 0-100
./z13-brightness-apply --to-windows 30        # Linux 30% ≈ how many Windows %?
./z13-camera-tool                             # list cameras labelled IR/RGB
```

Configuration is stored in `~/.config/z13-g2-tools/config.json`.

## GUI guide

The window title is **「Z13/Z16 Gen 2 工具箱」** (Z13/Z16 Gen 2 toolbox); the
main page is 「设置」 (Settings). A progress bar sits at the bottom of the
window: while a device write is in flight it shows a spinner and
「正在应用设置…」 ("applying settings…").

### Touchpad

- **Haptic intensity** (group 「触感强度」) — drag to adjust the vibration
  feedback; **snaps to 0 / 25 / 50 / 75 / 100** (matching the Windows
  driver); 「恢复默认」 (restore default) resets it to 50. The device
  register `0x00AB` and feature report 11 are mirrored by the firmware:
  writing either one updates the other.
- **Click force / release threshold** (group 「点击力度」) — 10-500 g; with
  「释放力度自动跟随」 (link release) on, the release threshold follows the
  click force at 65% (recommended).
- **Preset profiles** (「预设档位」) — 轻触 / 标准 / 重按 (light / standard /
  heavy), applied to the main zone immediately (110/72, 164/108, 240/156 g).
- **Top-button forces** (group 「顶部按键力度」) — per-button click / release
  for the top-edge left / middle / right zones: **click the touchpad graphic
  above** to pick a zone (or use the 「要调整的按键」 dropdown), then drag the
  「点击」 (click) and 「释放」 (release) sliders; **「按键预设」** (button
  presets) sets all three zones at once to 轻触 / 标准 / 重按
  (55/36, 76/50, 110/72 g; the standard step is the factory default).
- **Factory reset** — the 「恢复出厂设置」 (factory reset) button at the bottom
  writes all force and haptic settings back to firmware defaults (click
  164 g / release 108 g / zones 76 g / intensity 50 %).
- **Auto-restore** — turning on 「登录和唤醒后重新应用」 (re-apply at login and
  after wake) installs and enables two user services in
  `~/.config/systemd/user/`: `z13-touchpad-haptic.service` (apply at login)
  and `z13-touchpad-resume.service` (apply after suspend, via a
  `PrepareForSleep` D-Bus watcher).

### Companion tools (group 「配套工具」)

- **GNOME 快速设置开关** (GNOME Quick Settings toggle) — button reads
  「安装」 (install) when missing and runs `extensions/install.sh`; once
  installed it becomes 「卸载」 (uninstall), which removes the UUID from the
  enabled list and deletes the extension directory.
- **OLED 亮度** (OLED brightness) — slider writes the system backlight
  directly; when unavailable it shows 「不可用(权限或设备缺失)」 ("unavailable —
  permissions or device missing"); see
  [docs/brightness-permissions.md](docs/brightness-permissions.md).
- **相机检测** (camera detection) — 「重新检测」 (re-detect) button probes
  IR/RGB cameras on demand.
- **GPU 重置监控** (GPU-reset monitor) — read-only display of recent amdgpu
  resets (the persistent watcher is a systemd user service, see below).
- **硬件探测** (hardware probe) — 「运行」 (run) button executes `hw-probe.py`,
  writing [docs/hardware-findings.md](docs/hardware-findings.md).

### Developer mode

Toggle 「开发者信息」 (developer info) in the top-right ☰ menu, or launch
with `--developer`, to add two pages to the page switcher:

- **HID 功能** (HID features) — every feature report (read-only; report 7
  expands to show the 256 bytes as hex) plus all firmware registers
  (read-only, with defaults).
- **设备** (device) — hidraw path, HID_ID, HID_NAME, HID_PHYS, driver and
  kernel module.

## Command-line tools

### `z13-touchpad-apply` (main touchpad CLI)

No arguments = apply the saved config (what the login service runs; the
script has no `--help` — unknown arguments fall through to this too).

| Option | Meaning |
|---|---|
| `--get` | print the current haptic intensity (0-100) |
| `--set N` | set haptic intensity 0-100 and save it (feature-report path) |
| `--show` | show all firmware registers, modified ones marked `(modified)` |
| `--set-click-force G` | set the click force in grams and save it |
| `--set-click-release G` | set the release threshold in grams |
| `--set-haptic N` | set haptic intensity 0-100 via the register pipe |
| `--set-zone left\|right\|middle CLICK [RELEASE]` | set one zone's click (+release) force; omitted release = 65% of click |
| `--profile light\|standard\|heavy` | apply a preset (light 110/72, standard 164/108, heavy 240/156 g) |
| `--list-devices` | list all `/dev/hidraw*` with HID_NAME, marking the Sensel touchpad |
| `--reg 0xXXXX read` | read one firmware register (human-readable for known ones) |
| `--reg 0xXXXX VALUE` | write one register (grams/percent auto-converted for known ones, raw byte otherwise) |
| `--watch-test` | run `resume-watch.py --self-test` once (re-applies the config on success) |

### `z13-brightness-apply` (OLED brightness)

| Option | Meaning |
|---|---|
| `--get` | show the current brightness (percent + raw kernel value) |
| `--set N` | set the brightness percent 0-100 (clamped) |
| `--to-windows N` | Linux brightness N% ≈ Windows % |
| `--to-linux N` | Windows brightness N% ≈ Linux % |
| `--device PATH` | use a specific backlight sysfs dir (default: auto-detect) |

On permission failures it prints a clear Chinese error pointing to
[docs/brightness-permissions.md](docs/brightness-permissions.md).

### `z13-camera-tool` (camera detection)

| Option | Meaning |
|---|---|
| (none) | list cameras labelled IR/RGB with app-level guidance |
| `--json` | output the device list as JSON (for scripts) |
| `--no-pw` | skip PipeWire node probing |

IR is decided by a name containing IR (as a word) or a USB ID of
`04f2:b78c`; the RGB camera is `04f2:b78b`. See 「Camera switching」 below.

### `feature-probe.py` (experimental — dangerous)

Single-field read/writer for feature report 7 (the 256-byte Win8 PTP blob);
it snapshots the bank to `/tmp/z13_bank_snapshot.bin` before writing:

```bash
./feature-probe.py --dump                # show all feature reports
./feature-probe.py --read 7              # read report 7
./feature-probe.py --write 7 0x00        # write one field of report 7 (snapshotted first)
./feature-probe.py --restore             # restore the saved snapshot
./feature-probe.py --device /dev/hidrawN # explicit device (default: auto-detect)
```

**Only run from a graphical session while testing the touchpad by hand;
never write report 7 or unknown registers past this tool.**

### `gpu-reset-watch.py` (GPU-reset monitor)

Watches the kernel log for amdgpu GPU-reset / RAS events, appends
timestamped lines to `~/.config/z13-g2-tools/gpu-resets.log` and shows a
desktop notification when possible. See 「GPU-reset monitor」 below.

### `hw-probe.py` (hardware probe)

Probes the fingerprint reader (`06cb:0123`), TPM and IR/RGB cameras and
writes [docs/hardware-findings.md](docs/hardware-findings.md);
`--no-write` prints without writing.

### `resume-watch.py` (suspend watcher)

Listens on the D-Bus `PrepareForSleep` signal, re-runs `z13-touchpad-apply`
after wake and (after ~1.5 s) restores the kernel-reported display
brightness. `--self-test` runs the apply step once without touching D-Bus;
it is invoked by `z13-touchpad-apply --watch-test`.

## GNOME Quick Settings extension

### What it does

`extensions/z13-touchpad-quick@user` adds a 「触感强度」 (haptic intensity)
toggle to the GNOME Quick Settings panel (touchpad icon):

- On → `z13-touchpad-apply --set-haptic 100`; off → `--set-haptic 0`
- State → read back via `z13-touchpad-apply --get` (0-100, `>0` = on), the
  toggle is updated only after the subprocess exits
- It only calls the CLI — **no HID protocol is re-implemented in JS**

> Turning the toggle on sets the intensity to 100 (default); change the
> `ON_INTENSITY` constant at the top of `extension.js` for other values.

### Install & enable

```bash
cd extensions && ./install.sh
```

`install.sh` is idempotent (overwrites on reinstall): it writes the absolute
path of the repo's `z13-touchpad-apply` into the installed `cli-path` file
and appends the UUID to GNOME's enabled list
(`org.gnome.shell enabled-extensions`, preserving other extensions). The
extension looks for the CLI in this order: the `cli-path` file → `PATH` →
`~/.local/bin`, `~/bin`.

Because GNOME Shell scans the extension directories only **at startup**, a
freshly installed extension takes effect after **logging out and back in
(or rebooting)** — no manual step needed. Enabling manually still works:

```bash
gnome-extensions enable z13-touchpad-quick@user
```

(`Alt+F2` → `r` was removed in GNOME 42+, don't use it.)

### Uninstall

- From the GUI: Settings → 「配套工具」 → the button next to
  「GNOME 快速设置开关」 reads 「卸载」 — clicking it removes the UUID from the
  enabled list and deletes the extension directory.
- Manually: `gnome-extensions disable z13-touchpad-quick@user`, delete
  `~/.local/share/gnome-shell/extensions/z13-touchpad-quick@user`, and drop
  the UUID from `gsettings get org.gnome.shell enabled-extensions`.

### Notes & troubleshooting

- Verified on **GNOME 50.2**; `metadata.json` declares `shell-version`
  **45-50, but 45-49 is unverified**. After a major GNOME upgrade, if the
  toggle disappears check for API changes and update `shell-version`.
- Only the haptic-intensity toggle is implemented; the "touchpad enable"
  switch (HID report 6) is not wired up until its semantics are confirmed.
- State refreshes on enable and after each click; intensity changed in the
  GUI is reflected only after the next click or a reload.
- Toggle not showing: check the shell log
  `journalctl --user -b | grep z13-touchpad-quick`.
- Toggle inert: run `z13-touchpad-apply --get` by hand; check `cli-path` and
  device permissions.

## Brightness: permissions & scales

- Backlight device: `/sys/class/backlight/amdgpu_bl1` (type `raw`, measured
  on the Z13 Gen 2); detection picks the first non-LED panel backlight, or
  use `z13-brightness-apply --device PATH` to override.
- The `brightness` file is `root:root` and not writable by default, so a
  plain `--set` fails; reading is unaffected, and GNOME's brightness
  shortcuts/settings go through logind D-Bus and are not affected either.
  The recommended fix (a udev rule) is in
  [docs/brightness-permissions.md](docs/brightness-permissions.md).
- Linux ↔ Windows scale conversion (piecewise-linear interpolation on
  anchors, approximate):

| Linux % | 0 | 10 | 30 | 70 | 100 |
|---|---|---|---|---|---|
| Windows % | 0 | 20 | 75 | 95 | 100 |

- After suspend, `resume-watch.py` restores the kernel-reported brightness.

## Camera switching

Web apps tend to pick the IR camera (`04f2:b78c`) first, which yields a
black/failed picture. `z13-camera-tool` detects the cameras and tells you
how to switch:

- **In-app**: choose **"Integrated Camera"** (RGB) in the app's camera
  picker, avoiding "Integrated IR Camera".
- **Command-line v4l2 / gst apps**: `device=/dev/video0` (the RGB node;
  check the tool's output).
- **PipeWire apps**: `export PIPEWIRE_NODE='<RGB node name>'` — feasibility
  is probed live with `pw-dump` (confirmed working on this machine); apps
  using `xdg-desktop-portal` may ignore the variable.
- v4l2loopback is intentionally not used (needs root, too invasive).

## GPU-reset monitor

An amdgpu GPU reset (the driver auto-resets the GPU on faults) causes a
brief black screen / stutter, worth logging for later debugging.
`gpu-reset-watch.py`:

- matches kernel-log lines containing `GPU reset` or (amdgpu + RAS);
- appends timestamped lines to `~/.config/z13-g2-tools/gpu-resets.log`
  (directory auto-created);
- shows a desktop notification when `notify-send` is available and a
  graphical session exists (silently logs otherwise);
- can run as a systemd user service (example in the script's docstring):

```bash
systemctl --user enable --now z13-gpu-reset-watch.service
```

Note: `journalctl -k -f` requires the current user to be in the `adm` or
`systemd-journal` group. The GUI's 「GPU 重置监控」 row only displays the
records; the persistent watcher is provided by the service above (or by
running it in the foreground).

## Hardware probe

`hw-probe.py` read-only-probes the fingerprint reader, TPM and IR/RGB
cameras and writes [docs/hardware-findings.md](docs/hardware-findings.md)
(`--no-write` prints only). Current results on this Z13 Gen 2:

| Device | Status | Note |
|---|---|---|
| Fingerprint 06cb:0123 | untested | detected but no driver bound; fprintd may be unavailable |
| TPM | working | TPM 2.0, /dev/tpm0 present (access needs the `tss` group or root) |
| IR camera | working | /dev/video2, /dev/video3 (04f2:b78c) |
| RGB camera | working | /dev/video0, /dev/video1 (04f2:b78b) |

## How it works

The touchpad is an I2C-HID Sensel device (`2C2F:0027` / `2C2F:0028`),
detected by matching `SNSL002` or `00002C2F:0000002` in uevent. Two
interfaces are used:

1. **Feature reports** — haptic intensity = report 11 (HID feature `b0000`,
   0-100), read/written directly with `HIDIOCSFEATURE` / `HIDIOCGFEATURE`
   ioctls.
2. **Vendor register pipe** — click force & friends via report 0x09 (usage
   page `0xFF00`, usage `0x0001`): a 3-byte read/write command
   `[cmd_hi, cmd_lo, size]` + data + checksum, 21-byte frames, ACK-based
   (`1` = read OK, `5` = write OK). Register raw values are in **grams ÷ 2**.

All registers are **RAM-only** — they revert to defaults on reboot/suspend,
which is why the tool ships apply-at-login and after-wake services.

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

## Firmware registers

| Register | Name | Default | Meaning |
|---|---|---|---|
| `0x0038` | Click force | 164 g | force to trigger a click (the "click sensitivity") |
| `0x0090` | Click release | 108 g | force to release the click (hysteresis) |
| `0x0091`-`0x0092` | Left zone click / release | 76 g / 50 g | left-button zone |
| `0x0093`-`0x0094` | Right zone click / release | 76 g / 50 g | right-button zone |
| `0x0095`-`0x0096` | Middle zone click / release | 76 g / 50 g | middle-button zone |
| `0x00AB` | Haptic intensity | 50 % | mirrored with feature report 11 |
| `0x006E` | Haptics enabled | ON | master haptic switch |

Force range 10-500 g, intensity 0-100 %. Raw values are grams ÷ 2; all
writes are RAM-only.

## HID feature reports

| Report | Size | Usage | Meaning | Default |
|---|---|---|---|---|
| 3  | 1 B  | Vendor 0xFF00:0x01 | vendor parameter | `0x01` |
| 4  | 1 B  | Digitizers Inputmode | input mode (0-10) | `3` |
| 6  | 2 bit | Surface Switch / Button Switch | surface & button toggles (1 bit each) | `0` |
| 7  | 256 B| Vendor 0xFF00:0xC5 | Win8 PTP compliance blob | all `0x00` |
| 8  | 8 bit | Contact Max / Button Type | 4 bits each | Contact Max `5` |
| 10 | 1 bit | Digitizer Vendor 0x60 | vendor flag | `0` |
| 11 | 1 B  | Haptic: Intensity | haptic feedback strength (0-100) | `50` |
| 12 | 1 B  | Vendor 0xFF00:0x01 | vendor parameter | `0x00` |

## Troubleshooting / FAQ

- **CLI says it cannot open `/dev/hidrawN`** — the current user is not in
  the `plugdev` group and has no udev uaccess grant. Join `plugdev` and log
  back in, or verify seat uaccess. Note that hidraw1 is the Wacom touch
  screen — leave it alone.
- **Touchpad not found** — run `./z13-touchpad-apply --list-devices`;
  detection matches `SNSL002` or `00002C2F:0000002` in uevent.
- **Brightness `--set` fails** — permission problem; see
  [docs/brightness-permissions.md](docs/brightness-permissions.md) option A.
- **Extension toggle not showing** — log out and back in so the shell
  rescans extension directories; if it still fails check
  `journalctl --user -b | grep z13-touchpad-quick` and the
  `shell-version` in `metadata.json`.
- **Extension toggle inert** — run `z13-touchpad-apply --get` by hand; check
  the `cli-path` CLI is executable and device permissions are met.
- **Settings lost after reboot / suspend** — by design (registers are
  RAM-only). Turn on 「登录和唤醒后重新应用」 in the GUI, or check both user
  services: `systemctl --user status z13-touchpad-haptic.service z13-touchpad-resume.service`.
- **Touchpad dead after experimenting with the report-6 disable switch** —
  reboot to recover (registers are RAM-only); always keep a recovery path
  before experimental writes.

## Credits / Research

The click-force register interface was reverse-engineered by others; this
project verified their findings on a Z13 Gen 2 and wraps them in a friendly
GUI:

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
| `haptic.py` | device detection + feature-report ioctls + register pipe (stdlib only, core) |
| `gui.py` | GTK4/libadwaita GUI (settings + presets + zones + companion tools + developer mode) |
| `z13-touchpad-apply` | touchpad CLI (apply config / `--get` / `--set` / `--show` / `--set-click-*` / `--set-haptic` / `--set-zone` / `--profile` / `--reg` / `--list-devices` / `--watch-test`) |
| `z13-touchpad-tool` | GUI launcher (passes arguments through, e.g. `--developer`) |
| `feature-probe.py` | experimental report-7 (256B Win8 PTP blob) reader/writer with snapshot/restore (dangerous) |
| `resume-watch.py` | D-Bus `PrepareForSleep` watcher; re-applies settings + brightness after suspend |
| `brightness.py` | OLED backlight detection + read/write + Linux↔Windows scale (stdlib only) |
| `z13-brightness-apply` | brightness CLI (`--get` / `--set` / `--to-windows` / `--to-linux` / `--device`) |
| `z13-camera-tool` | IR/RGB camera detection + app-level switching guidance (`--json` / `--no-pw`) |
| `gpu-reset-watch.py` | kernel-log watcher that logs amdgpu resets + desktop notification |
| `hw-probe.py` | fingerprint / TPM / camera probe → `docs/hardware-findings.md` |
| `blob-sweep.py` | experimental report-7 byte sweeper with snapshot/restore (dangerous, not run) |
| `extensions/z13-touchpad-quick@user` | GNOME Quick Settings haptic toggle extension |
| `extensions/install.sh` | extension installer (idempotent, writes the enabled list) |
| `docs/` | permission notes + hardware findings |
| `tests/test_gui_e2e.py` | driver-level e2e tests (28 cases, needs DISPLAY: `GDK_BACKEND=x11 python3 tests/test_gui_e2e.py`) |

## License

MIT — see [LICENSE](LICENSE).
