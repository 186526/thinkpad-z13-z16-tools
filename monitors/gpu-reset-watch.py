#!/usr/bin/env python3
"""监控 amdgpu GPU reset 并记录/通知(ThinkPad Z13 Gen 2)。

用法:
    gpu-reset-watch.py            前台运行,持续监控(配合 systemd 用户服务)
    gpu-reset-watch.py --selftest 单元自测(过滤 + 日志逻辑,不碰内核日志)

检测通道:
    1. journalctl -k -f:需要当前用户在 adm 或 systemd-journal 组;启动时
       探测,不可读则降级到 /dev/kmsg。
    2. /dev/kmsg:非 root 通常 EPERM(需 CAP_SYSLOG),journald 占用时 EBUSY。
       两条通道都不可用时退出并给出中文指引(见输出)。

事件处理:
    - 匹配 "GPU reset" 或 (amdgpu + RAS) 的行;
    - notify-send 可用且存在图形会话时弹通知(缺失时静默跳过,仅记录);
    - 追加时间戳行到 ~/.config/z13-g2-tools/gpu-resets.log(目录自动创建,
      运行时状态,不写进仓库)。

systemd 用户服务安装说明(仅文档,脚本不自动安装;仿 set_autostart() 风格,
服务文件写入 ~/.config/systemd/user/):

    mkdir -p ~/.config/systemd/user
    cat > ~/.config/systemd/user/z13-gpu-reset-watch.service <<'EOF'
    [Unit]
    Description=Watch amdgpu GPU resets and log/notify
    After=graphical-session.target
    PartOf=graphical-session.target

    [Service]
    Type=simple
    ExecStart=/usr/bin/env python3 /path/to/gpu-reset-watch.py
    Restart=on-failure

    [Install]
    WantedBy=graphical-session.target
    EOF
    systemctl --user daemon-reload
    systemctl --user enable --now z13-gpu-reset-watch.service
    systemctl --user status z13-gpu-reset-watch.service

若日志里只有 "无法读取内核日志" 的提示,说明当前用户不在
adm/systemd-journal 组,请先(由用户执行):
    sudo usermod -aG systemd-journal $USER
然后重新登录再启用服务。
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

CONFIG_DIR = os.path.expanduser("~/.config/z13-g2-tools")
LOG_FILE = os.path.join(CONFIG_DIR, "gpu-resets.log")


def filter_reset(line):
    """判断一行内核日志是否 amdgpu GPU reset / RAS 事件。"""
    low = line.lower()
    return "gpu reset" in low or ("amdgpu" in low and "ras" in low)


def handle_event(line):
    """记录事件(时间戳 + 原文)并尝试弹通知。"""
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} | {line}\n")
    except OSError as e:
        print(f"写入日志失败: {e}", file=sys.stderr)
    notify(line)


def notify(line):
    """notify-send 可用且存在图形会话时弹通知,失败静默。"""
    if not shutil.which("notify-send"):
        return
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    body = (line[:200] + "…") if len(line) > 200 else (line or "(空)")
    try:
        subprocess.run(["notify-send", "amdgpu GPU 重置", body],
                       timeout=5, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _in_journal_groups():
    """当前用户是否在 adm / systemd-journal 组(可读内核日志的前提)。"""
    gids = set(os.getgroups())
    try:
        with open("/etc/group", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 3 and parts[0] in ("adm", "systemd-journal"):
                    try:
                        if int(parts[2]) in gids:
                            return True
                    except ValueError:
                        pass
    except OSError:
        pass
    return False


def pick_channel():
    """返回 ("journal", cmd) 或 ("kmsg", path);都不可用返回 None。"""
    if shutil.which("journalctl") and _in_journal_groups():
        return ("journal",
                ["journalctl", "-k", "-f", "-n", "0", "-q", "--no-pager"])
    if os.path.exists("/dev/kmsg"):
        try:
            fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
            os.close(fd)
            return ("kmsg", "/dev/kmsg")
        except OSError:
            pass
    return None


def _stream_journal(cmd):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if filter_reset(line):
                handle_event(line)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


def _stream_kmsg(path):
    import select
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    buf = b""
    try:
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines.pop()
                for ln in lines:
                    line = ln.decode("utf-8", "replace")
                    if filter_reset(line):
                        handle_event(line)
            else:
                select.select([fd], [], [], 0.5)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
    return 0


def run(channel):
    if channel[0] == "journal":
        return _stream_journal(channel[1])
    return _stream_kmsg(channel[1])


def selftest():
    """过滤 + 日志逻辑单元测试(不碰内核日志)。"""
    import tempfile

    ok = [
        "amdgpu: GPU reset begin!",
        "[drm] GPU reset begin",
        "amdgpu: GPU reset succeeded, try again",
        "amdgpu: RAS error occurred on IP block",
    ]
    bad = [
        "iwlwifi 0000:00:14.3: firmware reset",
        "amdgpu: driver loaded",
        "usb 1-5: reset high-speed USB device",
        "audit: type=1130 audit(...)",
    ]
    for t in ok:
        assert filter_reset(t), f"应匹配但未匹配: {t}"
    for t in bad:
        assert not filter_reset(t), f"不应匹配但匹配了: {t}"

    global LOG_FILE, CONFIG_DIR
    tmp = tempfile.mkdtemp(prefix="gpu-reset-test-")
    CONFIG_DIR = tmp
    LOG_FILE = os.path.join(tmp, "gpu-resets.log")
    handle_event("amdgpu: GPU reset begin!")
    with open(LOG_FILE, encoding="utf-8") as f:
        content = f.read()
    assert "GPU reset begin" in content, content
    ts, _, body = content.partition("|")
    assert "T" in ts, f"时间戳格式不对: {ts!r}"
    print("selftest 通过(过滤 + 日志追加)。测试日志:")
    print(content.strip())
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()

    channel = pick_channel()
    if channel is None:
        print("无法读取内核日志:当前用户不在 adm/systemd-journal 组,"
              "且 /dev/kmsg 不可读(通常需 CAP_SYSLOG)。", file=sys.stderr)
        print("解决办法(由用户执行):sudo usermod -aG systemd-journal $USER,"
              "重新登录后重试;或用 root 运行本脚本。", file=sys.stderr)
        print("systemd 用户服务安装说明见脚本头部 docstring。", file=sys.stderr)
        return 1

    print(f"监控通道: {'journalctl -k -f' if channel[0] == 'journal' else '/dev/kmsg'}")
    print(f"事件写入 {LOG_FILE};notify-send 可用时弹通知。Ctrl+C 退出。", flush=True)
    return run(channel)


if __name__ == "__main__":
    sys.exit(main())
