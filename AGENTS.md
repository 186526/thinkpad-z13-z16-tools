# AGENTS.md

ThinkPad Z13/Z16 Gen 2 综合性工具(触控板 + 亮度 + 相机 + 监控等)的项目操作手册。
本文件给 AI agent 提供在此仓库工作的约定、硬件访问模型与安全规则。

## 项目概览

以 ThinkPad Z13/Z16 Gen 2 的 Sensel 触觉触控板为核心的综合性工具箱
(GUI + CLI,窗口标题「Z13/Z16 Gen 2 工具箱」):

- 触感强度(feature 报告 11,五档吸附 0/25/50/75/100)、点击力度/释放阈值
  (固件寄存器)、顶部三键分区力度(含按键预设 light/standard/heavy)、
  主区力度预设(light/standard/heavy)
- 登录时与休眠唤醒后自动重新应用(所有设置都是内存态,重启/挂起即丢)
- HID feature 报告查看器(开发者模式)
- GUI「配套工具」分组一键入口:GNOME 快速设置扩展安装/卸载、OLED 亮度
  滑块、相机检测、GPU reset 记录、硬件探测
- 参考:[Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2)

## 文件布局

| 文件 | 用途 |
|---|---|
| `z13_tools/haptic.py` | 设备检测 + feature 报告 ioctl + 寄存器管道(仅标准库,核心) |
| `z13_tools/brightness.py` | OLED 背光探测 + 读写 + Linux↔Windows 刻度换算(仅标准库) |
| `z13_tools/gui/app.py` | GTK4/libadwaita GUI 入口:MainWindow / App / 页面装配,re-export `utils` 与 `touchpad_map`(异步写入队列 + 进度条) |
| `z13_tools/gui/utils.py` | GUI 的非 GTK 支撑逻辑:常量、配置读写、systemd 自启动、配套工具、feature 格式化(仅标准库 + haptic) |
| `z13_tools/gui/touchpad_map.py` | 可视化触控板自绘组件(`TouchpadMap`,GTK + pycairo) |
| `bin/z13-touchpad-apply` | CLI:应用配置 / `--get` / `--set` / `--show` / `--set-click-*` / `--set-haptic` / `--set-zone` / `--profile` / `--reg` / `--list-devices` / `--watch-test` |
| `bin/z13-touchpad-tool` | GUI 启动脚本(bash) |
| `bin/z13-brightness-apply` | 亮度 CLI(`--get` / `--set` / `--to-windows` / `--to-linux`) |
| `bin/z13-camera-tool` | IR/RGB 相机检测 + 应用级切换引导 |
| `bin/z13-hw-probe` | 指纹 / TPM / 摄像头探测,写 `docs/hardware-findings.md` |
| `monitors/resume-watch.py` | D-Bus `PrepareForSleep` 监听,唤醒后重跑 `bin/z13-touchpad-apply` 并恢复亮度 |
| `monitors/gpu-reset-watch.py` | 内核日志监听,记录 amdgpu reset |
| `experiments/feature-probe.py` | 实验性 report-7(256B Win8 PTP blob)读写器,带快照/还原 |
| `experiments/blob-sweep.py` | 实验性 report-7 逐字节扫描器,带快照/还原(危险,未运行) |
| `packaging/build-appimage.sh` | AppImage 构建脚本(本地与 CI 共用) |
| `packaging/icon-*.png` | 应用图标 |
| `extensions/` | GNOME 快速设置触感开关扩展(`z13-touchpad-quick@user`)+ 安装脚本 |
| `docs/` | 权限说明(`brightness-permissions.md`)+ 硬件探测结果 |
| `README.md` / `README.zh.md` | 文档,中英两份需保持同步 |

## 硬件访问模型

- 触控板是 I2C-HID Sensel 设备,`SNSL0028:00 2C2F:0028`(或 0027),本机在
  `/dev/hidraw0`(hid-multitouch 驱动);hidraw1 是 Wacom 触屏,不要碰。
- 检测:`haptic.find_device()` 匹配 uevent 里的 `SNSL002` 或 `00002C2F:0000002`。
- 权限:`/dev/hidrawN` 为 `root:plugdev 660`,需在 `plugdev` 组或获得
  udev uaccess 授权。工具用 `HIDIOCGFEATURE` / `HIDIOCSFEATURE` ioctl 读写。
- 两条通道:
  1. **feature 报告**(报告 11 触感强度 0-100 等)—— ioctl 直接读写。
  2. **厂商寄存器管道**(报告 0x09,用法页 0xFF00:0x01)—— 3 字节命令
     `[cmd_hi, cmd_lo, size]` + 数据 + 校验和,21 字节帧,ACK `1`=读成功
     `5`=写成功。力度寄存器原始值 = **克数 ÷ 2**。
- 实测事实(本机验证):feature 11 与寄存器 `0x00AB` 由固件**双向同步**。

### 寄存器表(z13_tools/haptic.py `REGISTERS`)

| 地址 | key | 含义 | 默认 |
|---|---|---|---|
| `0x0038` | click_force | 点击力度 | 164g |
| `0x0090` | click_release | 释放阈值 | 108g |
| `0x0091`-`0x0092` | zone_left / zone_left_release | 左区 | 76g / 50g |
| `0x0093`-`0x0094` | zone_right / zone_right_release | 右区 | 76g / 50g |
| `0x0095`-`0x0096` | zone_middle / zone_middle_release | 中区 | 76g / 50g |
| `0x00AB` | haptic_intensity | 触感强度 | 50% |
| `0x006E` | haptics_enabled | 触感总开关 | 1 |

feature 报告:3, 4, 6(Surface/Button Switch 各 1 bit), 7(256B blob),
8(Contact Max / Button Type), 10, 11(触感强度), 12。

## 配置与 systemd 服务

- 配置:`~/.config/z13-g2-tools/config.json`(键与 `REGISTERS_BY_KEY` 对齐)
- 用户服务(由 `gui.set_autostart()` 安装/移除,模板在 `z13_tools/gui/utils.py`):
  - `z13-touchpad-haptic.service` —— oneshot,登录时应用配置
  - `z13-touchpad-resume.service` —— 常驻 `monitors/resume-watch.py`,唤醒后应用
- CLI 默认无参数 = 应用已保存配置;`PERSISTED` 元组决定应用哪些键。

## 约定

- `z13_tools/haptic.py` 与 CLI **仅用 Python 3 标准库**;GUI 用 PyGObject
  (GTK4 + libadwaita)。
- UI 文案为**中文**(项目语言);代码注释中英皆可,新代码保持现状风格。
- CLI 输出人类可读的中文消息;错误走 stderr。
- 新配置键必须同时加入 `bin/z13-touchpad-apply` 的 `PERSISTED`(如需登录时恢复)。
- 设备写入全是内存态、可逆,但**写设备状态前仍须向用户确认**,除非任务
  已明确授权;实验类写入必须"改前快照、测后恢复"。
- 不要运行 `git commit` / `push` / 其它 git 变更,除非用户明确要求。
- 不要把运行时状态(配置、快照)写进仓库。

## 无头验证(agent 主要手段)

```bash
python3 -m py_compile z13_tools/__init__.py z13_tools/haptic.py \
    z13_tools/brightness.py z13_tools/gui/__init__.py \
    z13_tools/gui/app.py z13_tools/gui/utils.py z13_tools/gui/touchpad_map.py \
    bin/z13-touchpad-apply bin/z13-brightness-apply bin/z13-camera-tool \
    bin/z13-hw-probe monitors/resume-watch.py monitors/gpu-reset-watch.py \
    experiments/feature-probe.py experiments/blob-sweep.py \
    tests/test_gui_e2e.py
./bin/z13-touchpad-apply --get          # 触感强度
./bin/z13-touchpad-apply --show         # 全部寄存器
./bin/z13-touchpad-apply --list-devices # 全部 hidraw 设备
./bin/z13-touchpad-apply --watch-test   # 唤醒监听器自检
./experiments/feature-probe.py --dump   # 全部 feature 报告
python3 -m z13_tools.brightness         # 亮度换算单元测试
python3 -c "from z13_tools import haptic; print(haptic.find_device())"
GDK_BACKEND=x11 python3 tests/test_gui_e2e.py  # GUI 驱动级 e2e(29 用例,需 DISPLAY)
systemctl --user status z13-touchpad-haptic.service z13-touchpad-resume.service
```

- GUI / GNOME 扩展无法无头验证:用单元级调用代替(如
  `from z13_tools.gui import app as gui` 后 monkeypatch `gui.CONFIG_FILE` 到
  临时路径测 `save_config`),涉及界面交互的部分交付后留给用户实测。
- 设备状态改动后必须回读确认,实验后恢复原值。

## 安全红线

- **不要**绕过 `experiments/feature-probe.py` 直接写 report 7(256B blob)
  或未知寄存器;
  该工具写前自动存快照,可用 `--restore` 还原(重启也能重置)。
- 实验"触控板禁用"类开关(报告 6)前先和用户确认——可能把触控板锁死
  直到重启,务必预留恢复手段。
- 本机是用户唯一的实测目标机,禁止任何可能损坏设备的写入。
