# ThinkPad Z13 / Z16 Gen 2 触控板工具

面向联想 ThinkPad Z13 Gen 2 / Z16 Gen 2 上 **Sensel 触感触控板** 的小型
GUI + CLI 工具：

- **触感强度** 滑块（0-100，设备默认 50），拖动即时生效
- **点击力度** 滑块（10-500g，默认 164g）—— 即长期未公开的"点击灵敏度"，
  现在可调
- **释放阈值**，可自动按点击力度的 65% 联动
- **登录后自动应用 + 休眠唤醒恢复**：两个 systemd 用户服务在登录时和
  休眠唤醒后重新写入（设备上的设置全部是内存态，重启/休眠后恢复默认）
- **HID 功能查看器**：只读展示触控板暴露的全部 feature 报告和固件寄存器

> 参考资料：[Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2 – Haptic Touchpad](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2#Haptic_Touchpad)

## 原理

触控板是 I2C-HID 的 Sensel 设备（`2C2F:0027` / `2C2F:0028`），使用两条通道：

1. **触感强度** —— HID feature `b0000`（*Haptic: Intensity*，报告 ID 11，
   范围 0-100），通过 `HIDIOCSFEATURE` ioctl 写入。
2. **点击力度等参数** —— 固件寄存器，通过 **厂商 HID 管道**（报告 ID 0x09，
   用法页 `0xFF00` 用法 `0x0001`）访问：3 字节读写命令
   `[cmd_hi, cmd_lo, size]` + 数据 + 校验和，21 字节帧，带 ACK
   （`1`=读成功，`5`=写成功）。寄存器原始值按 **克 ÷ 2** 编码。

所有寄存器都是**内存态**，重启/休眠后恢复默认——所以工具带一个登录时
自动应用的服务。

在 ThinkPad Z13 Gen 2 上实测验证的事实：

- 触控板**不上报任何 force/pressure 数据轴**。点击完全由固件生成：按压力
  超过寄存器 `0x0038` 的阈值时，固件播放触感振动并把输入报告 3 里的一个
  按钮位置 1。因此"点击灵敏度"是**固件侧的力度阈值**，libinput 无法调节。
- 256 字节的厂商 feature 页 `0xFF00:0xC5` 其实是 Windows Precision
  Touchpad 的 "Win8 合规 blob"（内核 `hid-multitouch` 和 `hhd` 项目都这么
  处理）——点击灵敏度**不在**这里面；Arch Wiki 在这里没有进展是设计使然。
- 触感强度寄存器 `0x00AB` 与 feature 报告 11 由固件**双向同步**：
  写任一通道，另一通道随之更新（本机实测验证）。

## 依赖

- Python 3.9+（`haptic.py` / CLI 仅需标准库）
- GUI 需要 PyGObject（`python3-gi`）、GTK4、libadwaita
  （Debian: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`）
- 对 `/dev/hidraw*` 的读写权限（如 `plugdev` 组成员，或登录席位下的
  udev uaccess 授权）

## 使用

```bash
# GUI
./z13-touchpad-tool

# CLI —— 触感强度（feature 报告通道）
./z13-touchpad-apply --get                    # 查看当前强度
./z13-touchpad-apply --set 80                 # 设置强度并保存

# CLI —— 固件寄存器（点击力度等）
./z13-touchpad-apply --show                   # 查看全部寄存器
./z13-touchpad-apply --set-click-force 100g   # 点击力度（克）
./z13-touchpad-apply --set-click-release 65g  # 释放阈值（克）
./z13-touchpad-apply --set-haptic 60%         # 经寄存器设置触感强度

# 应用已保存的配置（登录服务调用）
./z13-touchpad-apply

# 查看全部 HID feature
./feature-probe.py --dump
```

GUI 中：

- **点击力度** —— 拖动设置按压力度（10-500g）；联动开关打开时释放阈值
  自动跟随 65%
- **触感强度** —— 拖动调整触感反馈（0-100，默认 50）
- 「恢复默认」回到 50
- 打开「登录后自动应用」会在 `~/.config/systemd/user/` 安装并启用
  `z13-touchpad-haptic.service`（登录时应用）和
  `z13-touchpad-resume.service`（休眠唤醒后应用，基于 `PrepareForSleep`
  D-Bus 信号监听）
- 「HID 功能」页展示全部 feature 报告 + 所有固件寄存器的只读视图

配置保存在 `~/.config/z13-g2-tools/config.json`。

## 固件寄存器

| 寄存器 | 名称 | 默认值 | 含义 |
|---|---|---|---|
| `0x0038` | 点击力度 | 164g | 触发点击的按压力（即"点击灵敏度"） |
| `0x0090` | 释放阈值 | 108g | 松开点击的力度（迟滞） |
| `0x0091`-`0x0092` | 左区点击/释放 | 76g / 50g | 左键区域 |
| `0x0093`-`0x0094` | 右区点击/释放 | 76g / 50g | 右键区域 |
| `0x0095`-`0x0096` | 中区点击/释放 | 76g / 50g | 中键区域 |
| `0x00AB` | 触感强度 | 50% | 与 feature 报告 11 对应 |
| `0x006E` | 触感开关 | ON | 触感总开关 |

原始值按克 ÷ 2 编码，写入仅内存态。

## HID feature 报告一览

| 报告 | 大小 | 用法 | 含义 | 默认值 |
|---|---|---|---|---|
| 3  | 1 B   | Vendor 0xFF00:0x01 | 厂商参数 | `0x01` |
| 4  | 1 B   | Digitizers Inputmode | 输入模式 (0-10) | `3` |
| 6  | 2 bit | Surface Switch / Button Switch | 表面/按钮开关 | `0` |
| 7  | 256 B | Vendor 0xFF00:0xC5 | Win8 PTP 合规 blob | 全 `0x00` |
| 8  | 8 bit | Contact Max / Button Type | 各 4 bit | Contact Max `5` |
| 10 | 1 bit | Digitizer Vendor 0x60 | 厂商标志 | `0` |
| 11 | 1 B   | Haptic: Intensity | 触感强度 (0-100) | `50` |
| 12 | 1 B   | Vendor 0xFF00:0x01 | 厂商参数 | `0x00` |

## 致谢 / 研究

点击力度寄存器接口由其他项目逆向完成，本仓库在 Z13 Gen 2 上验证了它们的
结论，并封装成易用的 GUI：

- [glutamatt/sensel-touchpad-linux](https://github.com/glutamatt/sensel-touchpad-linux)
  —— Linux 工具，寄存器表和 report 0x09 管道协议
- [daniel-bavrin/cirque-fix](https://github.com/daniel-bavrin/cirque-fix)
  —— 独立的 Windows 侧实现（反编译厂商 "Cirque Touchpad Custom Settings"
  应用里的 `SenselSerialDevice.dll`），寄存器与帧格式完全一致

两个项目逐字节吻合，本仓库在 Z13 Gen 2 上回读的结果也与文档默认值完全
一致。

## 文件

| 文件 | 用途 |
|---|---|
| `haptic.py` | 设备检测 + feature 报告 ioctl + 寄存器管道（仅标准库） |
| `gui.py` | GTK4/libadwaita GUI（设置 + HID 功能/寄存器查看器） |
| `z13-touchpad-apply` | CLI：应用已保存配置 / `--get` / `--set` / `--show` / `--set-click-*` |
| `z13-touchpad-tool` | GUI 启动脚本 |
| `feature-probe.py` | 实验性单字节参数区写入器 |
| `resume-watch.py` | D-Bus `PrepareForSleep` 监听器，休眠唤醒后重新应用设置 |

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
