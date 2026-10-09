# AGENTS.md

ThinkPad Z13/Z16 Gen 2 综合性工具(触控板 + 亮度 + 相机 + 监控等)的项目操作手册。
本文件给 AI agent 提供在此仓库工作的约定、硬件访问模型与安全规则。

## 项目概览

以 ThinkPad Z13/Z16 Gen 2 的 Sensel 触觉触控板为核心的综合性工具箱
(GUI + CLI,窗口标题「Z13/Z16 Gen 2 工具箱」,`application_id`
`io.github.thinkpad-z13-z16-tools`):

- 触感强度(feature 报告 11,五档吸附 0/25/50/75/100)、点击力度/释放阈值
  (固件寄存器)、顶部三键分区力度(含按键预设 light/standard/heavy)、
  主区力度预设(light/standard/heavy)
- 登录时与休眠唤醒后自动重新应用(所有设置都是内存态,重启/挂起即丢)
- HID feature 报告查看器(开发者模式)
- GUI 配套工具分三组:「开关与调节」(GNOME 快速设置扩展安装/卸载、OLED
  亮度滑块)、「检测与维护」(相机检测、硬件探测)、「状态与记录」(GPU reset
  记录,只读)
- 参考:[Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2)

当前版本 `z13_tools/__init__.py` 的 `__version__ = "0.2.0"`;Git tag 已有
`v0.1.0`、`v0.2.0`。

## 技术栈与构建

- **纯 Python 3**(开发机实测 3.14,README 声明 3.9+),**没有**
  `pyproject.toml` / `setup.py` / `package.json` / `Makefile` 等标准构建
  配置——不是 pip/打包项目。
- **核心库与 CLI 仅用标准库**:`z13_tools/haptic.py`、`z13_tools/brightness.py`
  以及 `bin/` 下全部 CLI。因此 `z13_tools/__init__.py` **刻意不导入 GUI 子包**,
  保证 `from z13_tools import haptic` 的导入链不依赖 PyGObject。
- **GUI 依赖 PyGObject**:GTK4 + libadwaita + pycairo(可选,cairo 缺失时
  渐变绘制降级)。Debian 依赖:`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`。
- **打包 = 手写 AppImage 脚本** `packaging/build-appimage.sh`(本地与 CI 共用):
  手工组装 AppDir(Python 解释器 + 标准库 + PyGObject/GTK4/libadwaita 系统库 +
  GI typelib + GSettings schema + Adwaita 图标 + 项目文件),再用 `appimagetool`
  打包。产物 `dist/thinkpad-z13-z16-tools-<VERSION>.AppImage`。
  - 用法:`VERSION=v0.1.0 APPIMAGETOOL=/path/to/appimagetool ./packaging/build-appimage.sh`
  - 打包目录:`z13_tools bin monitors experiments`(不含 `packaging/`、`docs/`)。
  - AppImage 内项目代码位于 `usr/share/z13-tools/`,保留源码树结构,使各脚本
    「向上找含 `z13_tools` 包的祖先目录」的定位算法同样成立。
  - AppImage 只提供 GUI;CLI 工具面向源码检出使用。

## CI / 发布

`.github/workflows/appimage.yml`(name: Build AppImage):

- 触发:push `v*` tag,或手动 `workflow_dispatch`。
- 运行于 `ubuntu-24.04`;装 `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
  python3-cairo libglib2.0-bin adwaita-icon-theme`,下载 `appimagetool`,
  跑 `packaging/build-appimage.sh`(tag 时 `VERSION=$GITHUB_REF_NAME`,
  手动触发为 `dev`)。
- 上传 artifact `dist/*.AppImage`;tag 时用 `gh release create/upload` 发布到
  Releases(需 `contents: write` 权限,`fetch-depth: 0` 供 `git describe` 用)。
- **不要在 agent 任务中主动跑 `git commit` / `push` / 打 tag**,除非用户明确要求。

## 文件布局

| 文件 | 用途 |
|---|---|
| `z13_tools/__init__.py` | 包定义 + `__version__`;不导入 GUI |
| `z13_tools/haptic.py` | 设备检测 + feature 报告 ioctl + 寄存器管道(仅标准库,核心) |
| `z13_tools/brightness.py` | OLED 背光探测 + 读写 + Linux↔Windows 刻度换算(仅标准库) |
| `z13_tools/camera.py` | video4linux 扫描 + IR/RGB 判定(`bin/z13-camera-tool` 与 `bin/z13-hw-probe` 共用,仅标准库) |
| `z13_tools/gui/app.py` | GTK4/libadwaita GUI 入口:MainWindow / App / 页面装配,re-export `utils` 与 `touchpad_map`(异步写入队列 + 进度条) |
| `z13_tools/gui/utils.py` | GUI 的非 GTK 支撑逻辑:常量、配置读写、systemd 自启动、配套工具、feature 格式化(仅标准库 + haptic) |
| `z13_tools/gui/touchpad_map.py` | 可视化触控板自绘组件(`TouchpadMap`,GTK + pycairo) |
| `bin/z13-touchpad-apply` | CLI:应用配置 / `--get` / `--set` / `--show` / `--set-click-*` / `--set-haptic` / `--set-zone` / `--profile` / `--reg` / `--list-devices` / `--watch-test` |
| `bin/z13-touchpad-tool` | GUI 启动脚本(bash,向上找仓库根) |
| `bin/z13-brightness-apply` | 亮度 CLI(`--get` / `--set` / `--to-windows` / `--to-linux` / `--device`) |
| `bin/z13-camera-tool` | IR/RGB 相机检测 + 应用级切换引导(`--json` / `--no-pw`) |
| `bin/z13-hw-probe` | 指纹 / TPM / 摄像头探测,写 `docs/hardware-findings.md`(`--no-write`) |
| `monitors/resume-watch.py` | D-Bus `PrepareForSleep` 监听,唤醒后重跑 `bin/z13-touchpad-apply` 并恢复亮度(`--self-test`) |
| `monitors/gpu-reset-watch.py` | 内核日志监听,记录 amdgpu reset + 桌面通知(`--selftest`) |
| `experiments/feature-probe.py` | 实验性 report-7(256B Win8 PTP blob)读写器,带快照/还原 |
| `experiments/blob-sweep.py` | 实验性 report-7 逐字节扫描器,带快照/还原(危险,未运行) |
| `packaging/build-appimage.sh` | AppImage 构建脚本(本地与 CI 共用) |
| `packaging/icon-*.png` | 应用图标(32/64/128/256/512) |
| `extensions/z13-touchpad-quick@user/` | GNOME 快速设置五档触感强度调节扩展(`extension.js` / `metadata.json` / `README.md`) |
| `extensions/install.sh` | 扩展安装脚本(幂等,写 `cli-path` + 追加启用列表) |
| `tests/test_gui_e2e.py` | GUI 驱动级 e2e(29 用例,需 DISPLAY) |
| `docs/brightness-permissions.md` | 背光写权限方案(udev / polkit) |
| `docs/hardware-findings.md` | 硬件探测结果(由 `bin/z13-hw-probe` 生成) |
| `PLAN.md` | 历史功能规划文档(12 项功能的阶段计划,供背景参考) |
| `README.md` / `README.zh.md` | 文档,中英两份需保持同步 |
| `AGENTS.md` | 本文件 |

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
- 实测事实(本机验证):feature 11 与寄存器 `0x00AB` 由固件**双向同步**;
  触控板不上报压力轴,点击完全由固件按 `0x0038` 阈值生成。

### 寄存器表(z13_tools/haptic.py `REGISTERS`)

| 地址 | key | 含义 | 默认(raw / 人类值) |
|---|---|---|---|
| `0x0038` | click_force | 点击力度 | 82 / 164g |
| `0x0090` | click_release | 释放阈值 | 54 / 108g |
| `0x0091` | zone_left | 左区点击 | 38 / 76g |
| `0x0092` | zone_left_release | 左区释放 | 25 / 50g |
| `0x0093` | zone_right | 右区点击 | 38 / 76g |
| `0x0094` | zone_right_release | 右区释放 | 25 / 50g |
| `0x0095` | zone_middle | 中区点击 | 38 / 76g |
| `0x0096` | zone_middle_release | 中区释放 | 25 / 50g |
| `0x00AB` | haptic_intensity | 触感强度 | 50 / 50% |
| `0x006E` | haptics_enabled | 触感总开关 | 1 / 1 |

力度范围 10-500g(intensity 0-100%);克数按 0.5g 步进,`from_human()` 用半进位
避免 Python 银行家舍入(165g → raw 83)。`PROFILES` 预设:light 110/72、
standard 164/108、heavy 240/156(单位 g)。

feature 报告(`FEATURE_REPORTS`):3, 4, 6(Surface/Button Switch 各 1 bit),
7(256B blob), 8(Contact Max / Button Type), 10, 11(触感强度), 12。

## 配置与 systemd 服务

- 配置:`~/.config/z13-g2-tools/config.json`(键与 `REGISTERS_BY_KEY` 对齐)。
- 用户服务(由 `gui.utils.set_autostart()` 安装/移除,单元模板在
  `z13_tools/gui/utils.py` 的 `_UNIT_APPLY` / `_UNIT_RESUME`):
  - `z13-touchpad-haptic.service` —— oneshot,登录时应用配置。
  - `z13-touchpad-resume.service` —— 常驻 `monitors/resume-watch.py`,唤醒后应用。
- GPU reset 监控服务为**文档说明**(脚本 docstring 给出示例,不自动安装)。
- CLI 默认无参数 = 应用已保存配置;`bin/z13-touchpad-apply` 的 `PERSISTED`
  元组决定登录时应用哪些键。
- 运行时状态(配置、`gpu-resets.log`、`blob-sweep.log`、快照)一律写在
  `~/.config/z13-g2-tools/` 或 `/tmp`,**不要**写进仓库(`.gitignore` 已忽略
  `.sisyphus/`,但运行产物本就该在仓库外)。

## 构建与测试命令

```bash
# 语法编译(全部 Python 入口;agent 最快的静态检查)
python3 -m py_compile z13_tools/__init__.py z13_tools/haptic.py \
    z13_tools/brightness.py z13_tools/camera.py z13_tools/gui/__init__.py \
    z13_tools/gui/app.py z13_tools/gui/utils.py z13_tools/gui/touchpad_map.py \
    bin/z13-touchpad-apply bin/z13-brightness-apply bin/z13-camera-tool \
    bin/z13-hw-probe monitors/resume-watch.py monitors/gpu-reset-watch.py \
    experiments/feature-probe.py experiments/blob-sweep.py \
    tests/test_gui_e2e.py

# 亮度模块自测(文件底部 assert 块:换算 + 背光探测)
python3 -m z13_tools.brightness

# GPU reset 监听自测(过滤 + 日志追加,不碰内核日志)
python3 monitors/gpu-reset-watch.py --selftest

# GUI 驱动级 e2e(29 用例,需真实 X 显示)
GDK_BACKEND=x11 python3 tests/test_gui_e2e.py

# 设备只读验证(需 plugdev 权限;不改变设备状态)
./bin/z13-touchpad-apply --get          # 触感强度
./bin/z13-touchpad-apply --show         # 全部寄存器
./bin/z13-touchpad-apply --list-devices # 全部 hidraw 设备
./bin/z13-touchpad-apply --watch-test   # 唤醒监听器自检(会重跑 apply)
./experiments/feature-probe.py --dump   # 全部 feature 报告
python3 -c "from z13_tools import haptic; print(haptic.find_device())"

# 服务状态
systemctl --user status z13-touchpad-haptic.service z13-touchpad-resume.service
```

- GUI / GNOME 扩展**无法无头验证**:用单元级调用代替(如
  `from z13_tools.gui import app as gui` 后 monkeypatch `gui.CONFIG_FILE` 到
  临时路径测 `save_config`),涉及界面交互的部分交付后留给用户实测。
- 设备状态改动后必须回读确认,实验后恢复原值。

## 代码组织与开发约定

- `z13_tools/haptic.py` 与 CLI **仅用 Python 3 标准库**;GUI 用 PyGObject
  (GTK4 + libadwaita)。新增非 GTK 逻辑放进 `gui/utils.py`,新增绘图组件
  放进 `gui/touchpad_map.py`,`app.py` 通过 `from ... import *` re-export 公共名。
- **测试对 `gui` 模块级名字做 monkeypatch**(`CONFIG_FILE`、`set_autostart`、
  `haptic.*`、`brightness.*`、配套工具函数等),因此 `app.py` 必须继续
  re-export `utils` 的名字;`load_config`/`save_config` 在 `app.py` 里包装,
  把 `app.CONFIG_FILE`(可被测试改写)显式传给 `gui_utils` 的实现。
- **UI 文案为中文**(项目语言);代码注释中英皆可,新代码保持现状风格。
- CLI 输出人类可读的中文消息;错误走 stderr;退出码 0/1。
- 每个 CLI / monitor / experiment 脚本顶部都有「仓库根 = 向上找含 `z13_tools`
  包的祖先目录」的定位代码——新增脚本沿用该模式,保证源码树与 AppImage 通用。
- 新增 CLI 选项 → 更新 `bin/z13-touchpad-apply` 的 docstring 用法说明。
- 新增配置键 → 若需登录时恢复,加入 `bin/z13-touchpad-apply` 的 `PERSISTED`
  元组。
- 新增 GUI 控件/页面 → 更新 `z13_tools/gui/app.py` 顶部模块 docstring。
- 更新文档时 `README.md` 与 `README.zh.md` 必须保持同步。
- 不要把运行时状态(配置、快照、日志)写进仓库。
- 不要运行 `git commit` / `push` / 其它 git 变更,除非用户明确要求。

### GUI 关键实现要点(改动时注意)

- 底部 `apply_bar` 是设备写入的进度反馈;写入走**串行异步队列**
  (`_enqueue_apply` → 后台线程 `_apply_worker` → `GLib.idle_add`
  `_finish_apply_queue`),写入失败会 `_load_state()` 重读设备回滚 UI。
- `selected_zone` 是唯一选中态数据源(0/1/2=左/中/右键,3=主点击区);
  图上点击、下拉、设备刷新都经 `_set_selected_zone()` 统一同步。
- 触感强度按 `INTENSITY_STEP=25` 吸附;防抖用 `GLib.timeout_add(400, ...)`,
  回填滑块时用 `handler_block_by_func` 抑制回调。
- 触控板图 `TouchpadMap` 不持有设备状态,数值/选中态由外部 `set_values` /
  `set_selected` / `set_main_force` 驱动,点击回调 `on_select(index)`。

## GNOME 扩展约定

- 扩展**只调用 `z13-touchpad-apply` CLI,不在 JS 里重实现 HID 协议**;
  写 `--set-haptic N`,读 `--get` 回读,子进程退出后才更新 UI。
- CLI 查找顺序:`install.sh` 写入的 `cli-path` → `PATH` → `~/.local/bin`、
  `~/bin`。
- 扩展在 **GNOME 50.2** 验证;`metadata.json` 声明 `shell-version` 45-50,
  但 45-49 未验证。GNOME 大版本升级后若开关不显示,检查 API 变化并更新
  `shell-version`。
- GNOME Shell 仅在**启动时**扫描扩展目录,新装扩展需**注销重登/重启**生效
  (`Alt+F2` → `r` 在 GNOME 42+ 已移除)。`install.sh` 幂等,会把 UUID 追加进
  `org.gnome.shell enabled-extensions`(保留其它扩展)。
- 只实现触感强度一项;「触控板开关」(报告 6)语义未确认,确认前不接入。

## 安全红线

- **不要**绕过 `experiments/feature-probe.py` 直接写 report 7(256B blob)
  或未知寄存器;该工具写前自动存快照,可用 `--restore` 还原(重启也能重置)。
- 实验「触控板禁用」类开关(报告 6)前先和用户确认——可能把触控板锁死
  直到重启,务必预留恢复手段。
- 写设备状态前**仍须向用户确认**,除非任务已明确授权;实验类写入必须
  「改前快照、测后恢复」。
- 本机是用户唯一的实测目标机,禁止任何可能损坏设备的写入。
- 需要 root 的系统级改动(udev/polkit 规则等)只写成文档,由用户执行,
  agent 不要自行安装。
