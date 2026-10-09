# ThinkPad Z13 / Z16 Gen 2 综合性工具箱

面向联想 ThinkPad Z13 Gen 2 / Z16 Gen 2 的**一个 GUI 管理所有功能**的工具箱:
触觉触控板(GUI + CLI)、OLED 亮度、IR/RGB 相机检测、GPU reset 监控、硬件
探测与 GNOME 快速设置扩展。核心是 Sensel 触觉触控板控制,其余功能围绕它
组成一套完整的日用工具箱。

## 项目简介

### 触控板(核心)

- **触感强度**滑块 —— 0-100,默认 50,**按 Windows 驱动五档吸附**
  (0 / 25 / 50 / 75 / 100);设备寄存器 `0x00AB` 与 feature 报告 11 双向同步
- **点击力度 / 释放阈值** —— 10-500g,默认 164g / 108g,可联动
  (释放自动跟随点击的 65%)
- **图内区域选择** —— 点击触控板图上左 / 中 / 右三键或主点击区,
  下方同一组滑块即编辑该区域
- **力度预设档位** —— 轻触 / 标准 / 重按,按当前选中的区域一键应用
  (主点击区 110/72、164/108、240/156g;顶部三键 55/36、76/50、110/72g)
- **异步写入 + 底部进度条** —— 设备写入在后台线程执行,不卡 UI
- **一键恢复出厂设置** —— 所有寄存器写回固件默认值
- **自动恢复** —— 登录时与休眠唤醒后自动重新应用(所有设置均为内存态,
  重启 / 挂起即丢)
- **HID 功能查看器 / 设备信息页**(开发者模式)—— 只读查看全部 feature
  报告、固件寄存器与设备信息

### 配套工具

- **OLED 亮度** —— GUI 滑块 + CLI,支持 Linux↔Windows 刻度换算
- **相机检测** —— 把 `/dev/video*` 标注为 IR / RGB,给出应用级切换引导
- **GPU reset 监控** —— 监听内核日志中的 amdgpu 重置并记录 / 通知
- **硬件探测** —— 指纹 / TPM / 摄像头探测,结果写入 `docs/hardware-findings.md`
- **GNOME 快速设置扩展** —— 快速设置面板里五档调节触感强度(0/25/50/75/100,
  参照内置"键盘"亮度项)
  (调 `z13-touchpad-apply` CLI,不重实现 HID 协议)

> 参考资料:[Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2 – Haptic Touchpad](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2#Haptic_Touchpad)

## 快速开始

### AppImage(推荐)

从 [Releases](https://github.com/186526/thinkpad-z13-z16-tools/releases) 下载最新
`thinkpad-z13-z16-tools-*.AppImage` —— 已内置 Python、PyGObject、GTK4 与
libadwaita,无需安装任何包(下方 `/dev/hidraw*` 权限要求仍然适用):

```bash
chmod +x thinkpad-z13-z16-tools-v0.2.0.AppImage
./thinkpad-z13-z16-tools-v0.2.0.AppImage
```

自行构建(需要 `appimagetool`):

```bash
VERSION=v0.2.0 APPIMAGETOOL=/path/to/appimagetool ./packaging/build-appimage.sh
```

注意:AppImage 提供的是 GUI;命令行工具(`z13-touchpad-apply` 等)请从源码
检出使用。CI 在每次推送 `v*` tag 时自动构建 AppImage 并附加到对应 release。

### 依赖

- Python 3.9+(`z13_tools/haptic.py` / CLI 仅需标准库)
- GUI 需要 PyGObject(`python3-gi`)、GTK4、libadwaita
  (Debian: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`)
- 对 `/dev/hidraw*` 的读写权限(如 `plugdev` 组成员,或登录席位下的
  udev uaccess 授权)

### 启动 GUI

```bash
./bin/z13-touchpad-tool            # 正常模式
./bin/z13-touchpad-tool --developer  # 开发者模式(追加 HID 功能 / 设备页)
```

### 命令行示例

```bash
# 触控板 —— 触感强度(feature 报告通道)
./bin/z13-touchpad-apply --get                    # 查看当前强度(0-100)
./bin/z13-touchpad-apply --set 80                 # 设置强度并保存

# 触控板 —— 固件寄存器(点击力度等)
./bin/z13-touchpad-apply --show                   # 查看全部寄存器
./bin/z13-touchpad-apply --set-click-force 100g   # 点击力度(克)
./bin/z13-touchpad-apply --set-click-release 65g  # 释放阈值(克)
./bin/z13-touchpad-apply --set-haptic 60%         # 经寄存器管道设置触感强度
./bin/z13-touchpad-apply --set-zone left 100 65   # 左区点击+释放(克;不写释放则取点击的 65%)
./bin/z13-touchpad-apply --profile heavy          # 应用预设(light|standard|heavy)

# 触控板 —— 设备与寄存器
./bin/z13-touchpad-apply --list-devices           # 列出全部 /dev/hidraw* 及 HID_NAME
./bin/z13-touchpad-apply --reg 0x0038 read        # 读一个固件寄存器
./bin/z13-touchpad-apply --reg 0x00AB 70          # 写一个寄存器(已知键自动换算克/百分比)
./bin/z13-touchpad-apply --watch-test             # 唤醒监听器自检一次(会重新应用配置)

# 应用已保存的配置(登录服务调用);无参数运行即此行为
./bin/z13-touchpad-apply

# 亮度 / 相机
./bin/z13-brightness-apply --get                  # 当前亮度
./bin/z13-brightness-apply --set 50               # 设置亮度 0-100
./bin/z13-brightness-apply --to-windows 30        # Linux 30% ≈ Windows 多少 %
./bin/z13-camera-tool                             # 列出相机并标注 IR/RGB
```

配置保存在 `~/.config/z13-g2-tools/config.json`。

## GUI 使用说明

窗口标题为 **「Z13/Z16 Gen 2 工具箱」**,页面顶部为「设置」页,底部有一条
写入进度条(设备写入期间显示 spinner 与「正在应用设置…」)。

### 触控板

- **触感强度**(分组「触感强度」)—— 拖动调整振动反馈,**按 25 吸附到
  0 / 25 / 50 / 75 / 100 五档**(与 Windows 驱动一致);「恢复默认」回到 50。
  设备寄存器 `0x00AB` 与 feature 报告 11 由固件双向同步,写任一通道另一
  通道随之更新。
- **按压力度**(分组「按压力度」)—— 一套滑块管整个触控板:**点击上方
  触控板图形**即可选择要调整的区域:上沿左 / 中 / 右三键或主点击区
  (或用「要调整的区域」下拉),「点击」「释放」滑块(10-500g)即编辑
  该区域;打开「释放力度自动跟随」后释放阈值按点击力度的 65% 联动(推荐)。
- **预设档位** —— 轻触 / 标准 / 重按,按当前选中的区域立即应用:主点击区
  用 110/72、164/108、240/156g(标准档即出厂默认);顶部三键统一用
  55/36、76/50、110/72g。
- **恢复出厂设置** —— 页面底部「恢复出厂设置」按钮,把所有力度与触感
  设置写回固件默认(点击 164g / 释放 108g / 分区 76g / 触感 50%)。
- **自动恢复设置** —— 打开「登录和唤醒后重新应用」会在
  `~/.config/systemd/user/` 安装并启用两个服务:
  `z13-touchpad-haptic.service`(登录时应用)与 `z13-touchpad-resume.service`
  (休眠唤醒后应用,基于 `PrepareForSleep` D-Bus 信号监听)。

### 配套工具(分组「开关与调节」/「检测与维护」/「状态与记录」)

- **GNOME 快速设置开关** —— 未安装时按钮为「安装」,点击运行
  `extensions/install.sh`;已安装时按钮变为「卸载」,点击后从启用列表移除
  并删除扩展目录。
- **OLED 亮度** —— 滑块直接写系统背光;不可用时显示
  「不可用(权限或设备缺失)」,授权方案见
  [docs/brightness-permissions.md](docs/brightness-permissions.md)。
- **相机检测** —— 「检测」按钮实时探测 IR / RGB 相机(首次探测成功后变为「重新检测」)。
- **GPU 重置监控** —— 只读展示最近的 amdgpu 重置记录(常驻监听由
  systemd 用户服务运行,见下文)。
- **硬件探测** —— 「运行」按钮执行 `bin/z13-hw-probe`,结果写入
  [docs/hardware-findings.md](docs/hardware-findings.md)。

### 开发者模式

菜单(右上角 ☰)→「开发者信息」开关,或启动时加 `--developer` 参数,
会向页面切换器追加两页:

- **HID 功能** —— 全部 feature 报告(只读;报告 7 展开查看 256 字节
  十六进制)+ 所有固件寄存器(只读,含默认值)。
- **设备** —— hidraw 路径、HID_ID、HID_NAME、HID_PHYS、驱动与内核模块。

## 命令行工具

### `z13-touchpad-apply`(触控板主 CLI)

无参数 = 应用已保存配置(供登录服务调用;本脚本无 `--help`,未知参数也
按此处理)。

| 参数 | 说明 |
|---|---|
| `--get` | 打印当前触感强度(0-100) |
| `--set N` | 设置触感强度 0-100 并保存(feature 报告通道) |
| `--show` | 显示全部固件寄存器,已改动的标 `(modified)` |
| `--set-click-force G` | 设置点击力度(克)并保存 |
| `--set-click-release G` | 设置释放阈值(克) |
| `--set-haptic N` | 经寄存器管道设置触感强度 0-100 |
| `--set-zone left\|right\|middle CLICK [RELEASE]` | 设置某分区点击(+释放)力度,缺省释放 = 点击的 65% |
| `--profile light\|standard\|heavy` | 应用预设(light 110/72、standard 164/108、heavy 240/156g) |
| `--list-devices` | 列出全部 `/dev/hidraw*` 及 HID_NAME,标注 Sensel 触控板 |
| `--reg 0xXXXX read` | 读一个固件寄存器(已知寄存器输出人类可读值) |
| `--reg 0xXXXX VALUE` | 写一个寄存器(已知寄存器自动换算克/百分比,未知寄存器取原始字节) |
| `--watch-test` | 运行 `resume-watch.py --self-test` 一次,自测完成会重新应用配置 |

### `z13-brightness-apply`(OLED 亮度)

| 参数 | 说明 |
|---|---|
| `--get` | 显示当前亮度(百分比 + 内核原始值) |
| `--set N` | 设置亮度百分比 0-100(超出自动钳制) |
| `--to-windows N` | Linux 亮度 N% ≈ Windows 亮度 % |
| `--to-linux N` | Windows 亮度 N% ≈ Linux 亮度 % |
| `--device PATH` | 指定背光 sysfs 目录(默认自动探测) |

写权限不足时输出明确中文错误并指向
[docs/brightness-permissions.md](docs/brightness-permissions.md)。

### `z13-camera-tool`(相机检测)

| 参数 | 说明 |
|---|---|
| (无) | 列出相机并标注 IR / RGB + 应用级切换引导 |
| `--json` | 以 JSON 输出设备清单(便于脚本消费) |
| `--no-pw` | 跳过 PipeWire 节点探测 |

IR 判定:名称含 IR(整词)或 USB ID 匹配 `04f2:b78c`;RGB 为 `04f2:b78b`。
详见下文「相机切换引导」。

### `experiments/feature-probe.py`(实验性,危险)

feature 报告 7(256 字节 Win8 PTP blob)的单字段读写器,写前自动保存快照
到 `/tmp/z13_bank_snapshot.bin`:

```bash
./experiments/feature-probe.py --dump                # 显示全部 feature 报告
./experiments/feature-probe.py --read 0              # 读报告 7 的第 N 字节(0-255)
./experiments/feature-probe.py --write 7 0x00        # 写报告 7 的一个字段(先快照)
./experiments/feature-probe.py --restore             # 还原快照
./experiments/feature-probe.py --device /dev/hidrawN # 指定设备(默认自动探测)
```

**危险实验:只能在图形会话中人工测试触控板行为时运行;不要绕过它直接写
report 7 或未知寄存器。**

### `monitors/gpu-reset-watch.py`(GPU reset 监控)

监听内核日志中的 amdgpu GPU reset / RAS 事件,追加时间戳行到
`~/.config/z13-g2-tools/gpu-resets.log`,可用时弹桌面通知。详见下文
「GPU reset 监控」。

### `bin/z13-hw-probe`(硬件探测)

探测指纹(`06cb:0123`)、TPM 与 IR / RGB 相机,结果写入
`docs/hardware-findings.md`;`--no-write` 只打印不写文件。

### `monitors/resume-watch.py`(唤醒监听)

D-Bus `PrepareForSleep` 监听:机器唤醒后重跑 `z13-touchpad-apply` 并(延迟
约 1.5 秒)恢复内核所报的显示亮度。`--self-test` 模式用于自检,由
`z13-touchpad-apply --watch-test` 调用。

## GNOME 快速设置扩展

### 功能

`extensions/z13-touchpad-quick@user` 在 GNOME 快速设置面板加入一个
「触感强度」调节项(图标为触控板,参照内置的"键盘"亮度项):

- 点整行 = 开关:关 ↔ 上次非零档位
- 点右侧菜单按钮弹出 **5 个档位按钮**(0/25/50/75/100,与固件吸附档
  一致),当前档位高亮,点选即写
- 写入 → `z13-touchpad-apply --set-haptic N`;状态 → `--get` 回读,在
  子进程退出后才更新 UI
- 只调用 CLI,**不在 JS 里重实现任何 HID 协议**

### 安装与启用

```bash
cd extensions && ./install.sh
```

`install.sh` 幂等(已存在则整体覆盖重装):把仓库内 `z13-touchpad-apply`
的绝对路径写进已安装目录的 `cli-path` 文件,并把 UUID 追加进 GNOME 启用
列表(`org.gnome.shell enabled-extensions`,保留其它已启用扩展)。扩展查找
CLI 的顺序:`cli-path` 文件 → `PATH` → `~/.local/bin`、`~/bin`。

由于 GNOME Shell 只在**启动时**扫描一次扩展目录,新安装的扩展要
**注销重新登录(或重启)**后才生效,无需手动操作。手动启用仍可用:

```bash
gnome-extensions enable z13-touchpad-quick@user
```

(GNOME 42+ 已移除 `Alt+F2` → `r` 的重载命令,别再用它。)

### 卸载

- GUI:设置页「开关与调节」→「GNOME 快速设置开关」按钮变为「卸载」,点击
  即从启用列表移除并删除扩展目录。
- 手动:`gnome-extensions disable z13-touchpad-quick@user`,删除
  `~/.local/share/gnome-shell/extensions/z13-touchpad-quick@user`,并从
  `gsettings get org.gnome.shell enabled-extensions` 中移除该 UUID。

### 注意事项与故障排查

- 本扩展在 **GNOME 50.2** 上验证;`metadata.json` 声明 `shell-version`
  **45-50,但 45-49 未验证**。GNOME 大版本升级后若开关不显示,先检查 API
  是否变化并更新 `shell-version`。
- 只做「触感强度」调节一项;「触控板开关」(HID 报告 6)语义尚未确认,确认前不接入。
- 状态只在启用时与每次点击后刷新;GUI 里改动强度后,开关状态到下次点击
  或重载扩展时才会同步。
- 开关不显示:查看 shell 日志
  `journalctl --user -b | grep z13-touchpad-quick`。
- 开关无效:手动跑 `z13-touchpad-apply --get` 检查 `cli-path` 与设备权限。

## OLED 亮度:权限与刻度

- 背光设备为 `/sys/class/backlight/amdgpu_bl1`(type=`raw`,Z13 Gen 2 实测);
  探测逻辑自动选「非 led 类型」的面板背光,也可用
  `z13-brightness-apply --device PATH` 显式指定。
- `brightness` 文件默认 `root:root` 不可写,普通用户 `--set` 会失败;读取
  不受影响,GNOME 的亮度快捷键 / 设置走 logind D-Bus 也不受影响。
  授权方案(推荐 udev 规则)见
  [docs/brightness-permissions.md](docs/brightness-permissions.md)。
- Linux ↔ Windows 刻度换算(锚点线性插值,近似):

| Linux % | 0 | 10 | 30 | 70 | 100 |
|---|---|---|---|---|---|
| Windows % | 0 | 20 | 75 | 95 | 100 |

- 休眠唤醒后,`monitors/resume-watch.py` 会把亮度恢复到内核所报的值。

## 相机切换引导

web 应用倾向优先选 IR 相机(`04f2:b78c`)导致画面失败 / 黑屏,`z13-camera-tool`
负责检测并给出切换引导:

- **应用内**:相机选择里选 **"Integrated Camera"**(RGB),避开
  "Integrated IR Camera"。
- **命令行 v4l2 / gst 应用**:`device=/dev/video0`(RGB 节点,以工具输出为准)。
- **PipeWire 应用**:`export PIPEWIRE_NODE='<RGB 节点名>'` —— 可行性由
  `pw-dump` 实时探测(本机已确认可行);经 `xdg-desktop-portal` 的应用可能
  忽略该变量。
- 不做 v4l2loopback(需 root,改动大)。

## GPU reset 监控

amdgpu GPU reset(如遇驱动异常自动重置 GPU)会造成短暂黑屏 / 卡顿,值得
留档排查。`monitors/gpu-reset-watch.py`:

- 匹配内核日志中 `GPU reset` 或 (amdgpu + RAS) 的行;
- 追加时间戳行到 `~/.config/z13-g2-tools/gpu-resets.log`(目录自动创建);
- `notify-send` 可用且存在图形会话时弹通知(缺失时静默,仅记录);
- `--selftest` 自测过滤与日志追加(不碰内核日志);
- 常驻方式(可选 systemd 用户服务,示例见脚本 docstring):

```bash
systemctl --user enable --now z13-gpu-reset-watch.service
```

注意:`journalctl -k -f` 需要当前用户在 `adm` 或 `systemd-journal` 组。
GUI 的「GPU 重置监控」行只读展示记录,常驻监听由上述服务(或手动前台
运行)提供。

## 硬件探测

`bin/z13-hw-probe` 只读探测指纹、TPM 与 IR / RGB 相机,把结果写入
[docs/hardware-findings.md](docs/hardware-findings.md)(`--no-write` 只打印)。
本机(Z13 Gen 2)当前结果摘要:

| 设备 | 状态 | 说明 |
|---|---|---|
| 指纹 06cb:0123 | 未测试 | 已检测到但接口未绑定驱动,fprintd 可能不可用 |
| TPM | 工作 | TPM 2.0,/dev/tpm0 存在(访问需 tss 组或 root) |
| IR 相机 | 工作 | /dev/video2、/dev/video3(04f2:b78c) |
| RGB 相机 | 工作 | /dev/video0、/dev/video1(04f2:b78b) |

## 原理

触控板是 I2C-HID 的 Sensel 设备(`2C2F:0027` / `2C2F:0028`),检测匹配
uevent 中的 `SNSL002` 或 `00002C2F:0000002`。使用两条通道:

1. **feature 报告** —— 触感强度 = 报告 11(HID feature `b0000`,0-100),
   经 `HIDIOCSFEATURE` / `HIDIOCGFEATURE` ioctl 直接读写。
2. **厂商寄存器管道** —— 点击力度等参数通过报告 0x09(用法页 `0xFF00`
   用法 `0x0001`)访问:3 字节读写命令 `[cmd_hi, cmd_lo, size]` + 数据 +
   校验和,21 字节帧,带 ACK(`1`=读成功,`5`=写成功)。寄存器原始值按
   **克 ÷ 2** 编码。

所有寄存器都是**内存态**,重启 / 休眠后恢复默认——所以工具带登录时与
唤醒后自动应用的服务。

实测验证的事实:

- 触控板**不上报任何 force/pressure 数据轴**。点击完全由固件生成:按压力
  超过寄存器 `0x0038` 的阈值时,固件播放触感振动并把输入报告 3 里的一个
  按钮位置 1。因此「点击灵敏度」是**固件侧的力度阈值**,libinput 无法调节。
- 256 字节的厂商 feature 页 `0xFF00:0xC5` 其实是 Windows Precision
  Touchpad 的 "Win8 合规 blob"(内核 `hid-multitouch` 与 `hhd` 项目都这么
  处理)——点击灵敏度**不在**这里面;Arch Wiki 在这里没有进展是设计使然。
- 触感强度寄存器 `0x00AB` 与 feature 报告 11 由固件**双向同步**:
  写任一通道,另一通道随之更新(本机实测验证)。

## 固件寄存器

| 寄存器 | 名称 | 默认值 | 含义 |
|---|---|---|---|
| `0x0038` | 点击力度 | 164g | 触发点击的按压力(即「点击灵敏度」) |
| `0x0090` | 释放阈值 | 108g | 松开点击的力度(迟滞) |
| `0x0091`-`0x0092` | 左区点击/释放 | 76g / 50g | 左键区域 |
| `0x0093`-`0x0094` | 右区点击/释放 | 76g / 50g | 右键区域 |
| `0x0095`-`0x0096` | 中区点击/释放 | 76g / 50g | 中键区域 |
| `0x00AB` | 触感强度 | 50% | 与 feature 报告 11 双向同步 |
| `0x006E` | 触感开关 | ON | 触感总开关 |

力度范围 10-500g,触感强度 0-100%;原始值按克 ÷ 2 编码,写入仅内存态。

## HID feature 报告一览

| 报告 | 大小 | 用法 | 含义 | 默认值 |
|---|---|---|---|---|
| 3  | 1 B   | Vendor 0xFF00:0x01 | 厂商参数 | `0x01` |
| 4  | 1 B   | Digitizers Inputmode | 输入模式 (0-10) | `3` |
| 6  | 2 bit | Surface Switch / Button Switch | 表面/按钮开关(各 1 bit) | `0` |
| 7  | 256 B | Vendor 0xFF00:0xC5 | Win8 PTP 合规 blob | 全 `0x00` |
| 8  | 8 bit | Contact Max / Button Type | 各 4 bit | Contact Max `5` |
| 10 | 1 bit | Digitizer Vendor 0x60 | 厂商标志 | `0` |
| 11 | 1 B   | Haptic: Intensity | 触感强度 (0-100) | `50` |
| 12 | 1 B   | Vendor 0xFF00:0x01 | 厂商参数 | `0x00` |

## 故障排查 / 常见问题

- **CLI 报「无法打开 /dev/hidrawN」** —— 当前用户不在 `plugdev` 组且未
  获得 udev uaccess 授权。加入 `plugdev` 组后重新登录,或确认登录席位
  uaccess。注意 hidraw1 是 Wacom 触屏,不要碰。
- **找不到触控板** —— 用 `./bin/z13-touchpad-apply --list-devices` 检查;
  检测匹配 uevent 里的 `SNSL002` 或 `00002C2F:0000002`。
- **亮度 `--set` 失败** —— 权限不足,见
  [docs/brightness-permissions.md](docs/brightness-permissions.md) 方案 A。
- **扩展开关不显示** —— 注销重新登录让 shell 重新扫描扩展目录;仍不行
  看 `journalctl --user -b | grep z13-touchpad-quick`,并核对
  `metadata.json` 的 `shell-version`。
- **扩展开关无效** —— 手动跑 `z13-touchpad-apply --get`;检查 `cli-path`
  指向的 CLI 是否可执行、设备权限是否满足。
- **设置重启 / 唤醒后丢失** —— 设计使然(寄存器全内存态)。打开 GUI 的
  「登录和唤醒后重新应用」,或确认两个 systemd 用户服务已启用:
  `systemctl --user status z13-touchpad-haptic.service z13-touchpad-resume.service`。
- **误操作触控板开关类实验(报告 6)后触控板失效** —— 重启即可恢复
  (寄存器为内存态);实验类写入务必先确认已预留恢复手段。

## 致谢 / 研究

点击力度寄存器接口由其他项目逆向完成,本仓库在 Z13 Gen 2 上验证了它们的
结论,并封装成易用的 GUI:

- [glutamatt/sensel-touchpad-linux](https://github.com/glutamatt/sensel-touchpad-linux)
  —— Linux 工具,寄存器表和 report 0x09 管道协议
- [daniel-bavrin/cirque-fix](https://github.com/daniel-bavrin/cirque-fix)
  —— 独立的 Windows 侧实现(反编译厂商 "Cirque Touchpad Custom Settings"
  应用里的 `SenselSerialDevice.dll`),寄存器与帧格式完全一致

两个项目逐字节吻合,本仓库在 Z13 Gen 2 上回读的结果也与文档默认值完全
一致。

## 文件

按功能分层:`z13_tools/` 为可导入的 Python 包(CLI 仅需标准库,因此包
`__init__` 刻意不导入 GUI);`bin/` 放 CLI 入口;`monitors/` 放常驻监控;
`experiments/` 放实验性(有风险)探测工具;`packaging/` 放 AppImage 构建。

| 路径 | 用途 |
|---|---|
| `z13_tools/haptic.py` | 设备检测 + feature 报告 ioctl + 寄存器管道(仅标准库,核心) |
| `z13_tools/brightness.py` | OLED 背光探测 + 读写 + Linux↔Windows 刻度换算(仅标准库) |
| `z13_tools/camera.py` | video4linux 扫描 + IR/RGB 判定(`bin/z13-camera-tool` 与 `bin/z13-hw-probe` 共用,仅标准库) |
| `z13_tools/gui/app.py` | GTK4/libadwaita GUI(设置 + 预设 + 分区 + 配套工具 + 开发者模式) |
| `z13_tools/gui/utils.py` | GUI 的非 GTK 支撑:常量、配置读写、systemd 自启动、配套工具 |
| `z13_tools/gui/touchpad_map.py` | 自绘可点击触控板组件(`TouchpadMap`) |
| `bin/z13-touchpad-apply` | 触控板 CLI(应用配置 / `--get` / `--set` / `--show` / `--set-click-*` / `--set-haptic` / `--set-zone` / `--profile` / `--reg` / `--list-devices` / `--watch-test`) |
| `bin/z13-touchpad-tool` | GUI 启动脚本(透传参数,如 `--developer`) |
| `bin/z13-brightness-apply` | 亮度 CLI(`--get` / `--set` / `--to-windows` / `--to-linux` / `--device`) |
| `bin/z13-camera-tool` | IR/RGB 相机检测 + 应用级切换引导(`--json` / `--no-pw`) |
| `bin/z13-hw-probe` | 指纹 / TPM / 摄像头探测 → `docs/hardware-findings.md` |
| `monitors/resume-watch.py` | D-Bus `PrepareForSleep` 监听,唤醒后重跑应用配置 + 恢复亮度 |
| `monitors/gpu-reset-watch.py` | 内核日志监听,记录 amdgpu reset + 桌面通知 |
| `experiments/feature-probe.py` | 实验性 report 7(256B Win8 PTP blob)读写器,带快照/还原(危险) |
| `experiments/blob-sweep.py` | 实验性 report 7 逐字节扫描器,带快照/还原(危险,未运行) |
| `packaging/build-appimage.sh` | AppImage 构建脚本(本地与 CI 共用) |
| `packaging/icon-*.png` | 应用图标 |
| `extensions/z13-touchpad-quick@user` | GNOME 快速设置五档触感强度扩展 |
| `extensions/install.sh` | 扩展安装脚本(幂等,写入启用列表) |
| `docs/` | 权限说明 + 硬件探测结果 |
| `tests/test_gui_e2e.py` | 驱动级 e2e 测试(29 用例,需 DISPLAY:`GDK_BACKEND=x11 python3 tests/test_gui_e2e.py`) |

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
