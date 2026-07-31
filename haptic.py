"""ThinkPad Z13/Z16 Gen 2 Sensel haptic touchpad control.

Reads/writes the "Haptic: Intensity" HID feature (b0000, report 11,
range 0-100) of the I2C-HID Sensel touchpad directly through
/dev/hidraw* using HIDIOCGFEATURE / HIDIOCSFEATURE ioctls.

No external dependencies beyond the Python standard library.

See: https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2#Haptic_Touchpad
"""

import fcntl
import glob
import os

# Linux hidraw ioctl numbers (from <linux/hidraw.h>)
_HIDIOCGFEATURE = 0xC0084807  # get feature report
_HIDIOCSFEATURE = 0xC0084806  # set feature report

# Feature b0000 -> HID report 11, one 8-bit field, range 0-100
_REPORT_ID = 0x0B
_INTENSITY_MIN = 0
_INTENSITY_MAX = 100


class TouchpadError(Exception):
    """Raised when the touchpad device cannot be accessed."""


def _matches_touchpad_uevent(data):
    """True when a uevent blob describes the Sensel haptic touchpad."""
    return "SNSL002" in data or "00002C2F:0000002" in data


def find_device():
    """Return the /dev/hidrawN path of the Sensel haptic touchpad, or None.

    Matches the I2C-HID node by its HID_ID (vendor 2C2F, product 0027/0028)
    or its HID_NAME (SNSL0028 / SNSL0027).
    """
    for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = os.path.join(hidraw, "device", "uevent")
        try:
            with open(uevent, encoding="utf-8", errors="replace") as f:
                data = f.read()
        except OSError:
            continue
        if _matches_touchpad_uevent(data):
            return "/dev/" + os.path.basename(hidraw)
    return None


def list_hidraw_devices():
    """Return [(path, HID_NAME, is_touchpad)] for every /dev/hidrawN.

    Uses the same uevent matching as find_device() to flag the touchpad.
    """
    out = []
    for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = os.path.join(hidraw, "device", "uevent")
        try:
            with open(uevent, encoding="utf-8", errors="replace") as f:
                data = f.read()
        except OSError:
            continue
        name = ""
        for line in data.splitlines():
            if line.startswith("HID_NAME="):
                name = line.split("=", 1)[1]
                break
        out.append(("/dev/" + os.path.basename(hidraw), name,
                    _matches_touchpad_uevent(data)))
    return out


def device_info(path=None):
    """Return a dict of sysfs info about the touchpad, or None if not found.

    Keys: DEVICE (/dev/hidrawN path), HID_ID, HID_NAME, HID_PHYS, DRIVER
    (parsed from the uevent file) and MODULE (driver kernel module name
    resolved from device/driver/module, '' when not available).
    """
    path = path or find_device()
    if path is None:
        return None
    sysdev = f"/sys/class/hidraw/{os.path.basename(path)}/device"
    uevent = os.path.join(sysdev, "uevent")
    info = {}
    try:
        with open(uevent, encoding="utf-8", errors="replace") as f:
            data = f.read()
    except OSError:
        return None
    for line in data.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("HID_ID", "HID_NAME", "HID_PHYS", "DRIVER"):
            info[key] = value
    info["DEVICE"] = path
    module_link = os.path.join(sysdev, "driver", "module")
    if os.path.islink(module_link):
        info["MODULE"] = os.path.basename(os.path.realpath(module_link))
    else:
        info["MODULE"] = ""
    return info


def _open(path):
    try:
        return os.open(path, os.O_RDWR)
    except OSError as e:
        raise TouchpadError(f"无法打开 {path}: {e.strerror}") from e


def get_intensity(path):
    """Read the current haptic intensity (0-100) from the device."""
    fd = _open(path)
    try:
        buf = bytes([_REPORT_ID, 0])
        result = fcntl.ioctl(fd, _HIDIOCGFEATURE, buf)
        return result[1]
    except OSError as e:
        raise TouchpadError(f"读取触感强度失败: {e.strerror}") from e
    finally:
        os.close(fd)


def set_intensity(path, value):
    """Set the haptic intensity (0-100) on the device."""
    value = int(value)
    if not _INTENSITY_MIN <= value <= _INTENSITY_MAX:
        raise ValueError(f"强度必须在 {_INTENSITY_MIN}-{_INTENSITY_MAX} 之间")
    fd = _open(path)
    try:
        fcntl.ioctl(fd, _HIDIOCSFEATURE, bytes([_REPORT_ID, value]))
    except OSError as e:
        raise TouchpadError(f"设置触感强度失败: {e.strerror}") from e
    finally:
        os.close(fd)


# (report_id, data_length) for every feature report the device exposes.
FEATURE_REPORTS = (
    (3, 1),    # vendor 0xFF00:0x01
    (4, 1),    # Digitizers Inputmode (0-10)
    (6, 1),    # Surface Switch / Button Switch (1 bit each)
    (7, 256),  # vendor 0xFF00:0xC5 parameter bank
    (8, 1),    # Contact Max / Button Type (4 bits each)
    (10, 1),   # digitizer vendor usage 0x60 (1 bit)
    (11, 1),   # Haptic: Intensity (0-100)
    (12, 1),   # vendor 0xFF00:0x01
)


def read_features(path=None):
    """Read every known feature report from the device.

    Returns {report_id: bytes} without the leading report-id byte.
    Reports the device refuses to return are omitted.
    """
    path = path or find_device()
    if path is None:
        raise TouchpadError("未找到触控板设备")
    fd = _open(path)
    out = {}
    try:
        for rid, size in FEATURE_REPORTS:
            buf = bytes([rid]) + b"\x00" * size
            try:
                res = fcntl.ioctl(fd, _HIDIOCGFEATURE, buf)
            except OSError:
                continue
            out[rid] = res[1:]
    finally:
        os.close(fd)
    return out


def bank_nonzero(data):
    """Offsets of non-zero bytes inside the report-7 parameter bank."""
    return [i for i, b in enumerate(data) if b]


# ─── Vendor register pipe (report 0x09) ─────────────────────────────────────
# The click-force threshold lives in firmware registers accessed through the
# vendor HID pipe (report ID 0x09, usage page 0xFF00 usage 0x0001). Protocol
# reverse-engineered by glutamatt (github.com/glutamatt/sensel-touchpad-linux)
# and daniel-bavrin (github.com/daniel-bavrin/cirque-fix); verified on the
# ThinkPad Z13 Gen 2. Values are RAM-only: they revert on reboot/suspend.

_PIPE_REPORT = 0x09
_PIPE_SIZE = 21
_READ_ACK = 0x01
_WRITE_ACK = 0x05


def _build_cmd(is_read, reg, size=1):
    byte0 = ((reg & 0x3F00) >> 7) | 1 | (0x80 if is_read else 0x00)
    return bytes([byte0, reg & 0xFF, size])


class Register:
    """A firmware register with human-friendly conversion."""

    def __init__(self, addr, key, name, unit, default, min_val, max_val):
        self.addr = addr
        self.key = key
        self.name = name
        self.unit = unit
        self.default = default
        self.min_val = min_val
        self.max_val = max_val

    @property
    def suffix(self):
        return {"grams": "g", "percent": "%", "bool": ""}[self.unit]

    def to_human(self, raw):
        if self.unit == "grams":
            return raw * 2
        return raw

    @staticmethod
    def _half_up(x):
        return int(x + 0.5)

    def from_human(self, value):
        # 克数寄存器按 0.5g/步编码：钳制到 [min_val, max_val] 对应的 raw 区间
        # （10-500g -> raw 5-250），半进位避免 round() 的银行家舍入
        # （165g 应得 raw 83，而不是 82）。
        if self.unit == "grams":
            raw_min = self._half_up(self.min_val / 2)
            raw_max = self._half_up(self.max_val / 2)
            return max(raw_min, min(raw_max, self._half_up(value / 2)))
        return max(self.min_val, min(self.max_val, self._half_up(value)))

    def fmt(self, raw):
        return f"{self.to_human(raw)}{self.suffix}"


REGISTERS = (
    Register(0x0038, "click_force", "点击力度", "grams", 82, 10, 500),
    Register(0x0090, "click_release", "释放阈值", "grams", 54, 10, 500),
    Register(0x0091, "zone_left", "左区点击力度", "grams", 38, 10, 500),
    Register(0x0092, "zone_left_release", "左区释放阈值", "grams", 25, 10, 500),
    Register(0x0093, "zone_right", "右区点击力度", "grams", 38, 10, 500),
    Register(0x0094, "zone_right_release", "右区释放阈值", "grams", 25, 10, 500),
    Register(0x0095, "zone_middle", "中区点击力度", "grams", 38, 10, 500),
    Register(0x0096, "zone_middle_release", "中区释放阈值", "grams", 25, 10, 500),
    Register(0x00AB, "haptic_intensity", "触感强度", "percent", 50, 0, 100),
    Register(0x006E, "haptics_enabled", "触感开关", "bool", 1, 0, 1),
)

REGISTERS_BY_KEY = {r.key: r for r in REGISTERS}
REGISTERS_BY_ADDR = {r.addr: r for r in REGISTERS}

# 预设力度档位（单位 g）：release 取 click 的 65% 附近，与主区联动逻辑一致。
# 注意 0.5g 步进：165g 会落成 raw 83 = 166g，属预期。
PROFILES = {
    "light": {"click_force": 110, "click_release": 72},
    "standard": {"click_force": 164, "click_release": 108},
    "heavy": {"click_force": 240, "click_release": 156},
}


class RegisterError(Exception):
    pass


def _pipe_open(path):
    try:
        return os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        raise RegisterError(f"无法打开 {path}: {e.strerror}") from e


def _pipe_write(fd, payload):
    buf = bytearray(_PIPE_SIZE)
    buf[0] = _PIPE_REPORT
    buf[1] = len(payload)
    buf[2:2 + len(payload)] = payload
    os.write(fd, bytes(buf))


def _flush(fd):
    import select
    while select.select([fd], [], [], 0.01)[0]:
        os.read(fd, _PIPE_SIZE)


def _collect(fd, min_bytes, timeout=1.0):
    import select
    import time
    result = bytearray()
    deadline = time.time() + timeout
    while len(result) < min_bytes and time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0.05, deadline - time.time()))
        if not ready:
            break
        buf = os.read(fd, _PIPE_SIZE)
        if len(buf) < 2 or buf[0] != _PIPE_REPORT:
            continue
        result.extend(buf[2:2 + buf[1]])
    return result


def read_register(path, reg):
    """Read one firmware register (1 byte). Returns the raw value."""
    fd = _pipe_open(path)
    try:
        _flush(fd)
        _pipe_write(fd, _build_cmd(True, reg, 1))
        resp = _collect(fd, 4 + 1 + 1)
        if len(resp) < 4:
            raise RegisterError(f"寄存器 0x{reg:04X} 无响应")
        if resp[0] != _READ_ACK:
            raise RegisterError(f"寄存器 0x{reg:04X} 读取失败 (ACK=0x{resp[0]:02X})")
        data_len = resp[2] | (resp[3] << 8)
        if len(resp) < 4 + data_len + 1:
            raise RegisterError(f"寄存器 0x{reg:04X} 响应不完整")
        data = resp[4:4 + data_len]
        if (sum(data) & 0xFF) != resp[4 + data_len]:
            raise RegisterError(f"寄存器 0x{reg:04X} 校验和错误")
        return data[0]
    finally:
        os.close(fd)


def write_register(path, reg, value):
    """Write one firmware register byte."""
    value = value & 0xFF
    fd = _pipe_open(path)
    try:
        _flush(fd)
        # payload: cmd(3) + data(1) + checksum(1); checksum of 1 byte = itself
        _pipe_write(fd, _build_cmd(False, reg, 1) + bytes([value, value]))
        resp = _collect(fd, 2)
        if len(resp) < 1 or resp[0] != _WRITE_ACK:
            ack = resp[0] if resp else -1
            raise RegisterError(f"寄存器 0x{reg:04X} 写入失败 (ACK=0x{ack:02X})")
    finally:
        os.close(fd)


def read_all_registers(path=None):
    """Read every known register. Returns {key: raw_value}."""
    path = path or find_device()
    out = {}
    for reg in REGISTERS:
        try:
            out[reg.key] = read_register(path, reg.addr)
        except RegisterError:
            out[reg.key] = None
    return out
