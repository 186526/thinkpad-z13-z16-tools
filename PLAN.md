# PLAN — Z13 工具新增 12 项功能(原 13 项,「3 · wiki 更新」已取消)

依据:[Arch Wiki – Lenovo ThinkPad Z13/Z16 Gen 2](https://wiki.archlinux.org/title/Lenovo_ThinkPad_Z13/Z16_Gen_2)
(仅作硬件参考资料,不向其提交任何内容——本地自用)

开工前先读 `AGENTS.md`(硬件访问模型、寄存器表、安全红线、无头验证手段)。
以下按依赖关系分 4 个阶段。功能号 1-12(原 13 项去掉「wiki 更新」后顺延
编号)。每项都含:目标 → 涉及文件 → 实施步骤 → 验证 → 风险。

**跨功能公共事项**(每做完一项都要做):

- 新增 CLI 选项 → 更新 `z13-touchpad-apply` 的 docstring 用法说明。
- 新增配置键 → 若需登录时恢复,加入 `apply.py` 的 `PERSISTED` 元组。
- 新增 GUI 控件/页面 → 更新 `gui.py` 顶部模块 docstring。
- 更新 `README.md` 与 `README.zh.md`(两份保持同步)。
- 所有脚本保持 `python3 -m py_compile` 通过。

---

## Phase 0 — 现状核对(必做,30 分钟)

1. 通读 `haptic.py`、`gui.py`、`z13-touchpad-apply`、`feature-probe.py`、
   `resume-watch.py`。
2. 无头验证基线:`./z13-touchpad-apply --get`、`--show`、`./feature-probe.py --dump`,
   确认设备在 `/dev/hidraw0`、所有寄存器可读。
3. 确认服务状态:`systemctl --user status z13-touchpad-haptic.service
   z13-touchpad-resume.service`(两个都应 enabled,resume 应 active)。
4. **功能 4(休眠唤醒恢复)已完成**,只做验证,不重复实现。

---

## Phase 1 — 触控板核心

### 功能 1:区域力度 GUI 化(左/右/中区点击与释放,0x0091-0x0096)

- **目标**:GUI 设置页新增"区域力度"组,6 个滑块(左/右/中 × 点击/释放),
  范围 10-500g;CLI 支持设置;登录时恢复。
- **涉及**:`gui.py`、`z13-touchpad-apply`、`haptic.py`(仅验证)、README。
- **步骤**:
  1. `haptic.py` 的 `REGISTERS` 已含全部 6 个区域寄存器(zone_left 等),
     直接复用 `write_register` / `read_register` 与 `from_human`/`to_human`。
  2. `z13-touchpad-apply`:仿 `--set-click-force` 增加
     `--set-zone <left|right|middle> <click_g> [release_g]`(缺省 release 取
     click 的 65%,与主区联动逻辑一致),或提供六个独立选项
     `--set-zone-left-click` 等——二选一,**推荐前者**(一个选项,少膨胀 CLI)。
     保存配置键:`zone_left`、`zone_left_release` 等(与 `REGISTERS_BY_KEY`
     一致)。
  3. `apply.py` `PERSISTED` 加入 6 个区域键。
  4. `gui.py`:设置页新增 `Adw.PreferencesGroup(title="区域力度")`,三对滑块
     (左/右/中,每对"点击力度 + 释放阈值"),复用 `_schedule_apply` 的
     400ms 防抖模式。写入成功后把滑块同步到设备真实值(`raw*2`),
     参考 `_apply_all` 的做法。`_load_state` 里回填当前值。
  5. 联动:主区 65% 联动开关**不**延伸到区域(区域独立,简单优先)。
- **验证**:CLI 设置左区 100g → `--show` 回读 100g;GUI 逻辑用单元级调用
  (monkeypatch `CONFIG_FILE`);最后恢复默认 76/50。
- **风险**:低。区域寄存器与主区同协议,已验证可写。

### 功能 2:触控板快速开关(报告 6,实验性)

- **目标**:一键禁用/启用触控板(打字防误触),CLI + GUI 开关。
- **依据**:HID 报告 6 = "Surface Switch / Button Switch",各 1 bit,当前 `0x00`。
- **涉及**:`feature-probe.py`(实验)、`haptic.py`(新函数)、`z13-touchpad-apply`、
  `gui.py`、README。
- **步骤**:
  1. **先做语义实验(必须,且先问用户)**:
     - `read_features()` 读报告 6 → 改 bit0(Surface Switch)=1 → 写回
       → 请用户触摸触控板确认是否失效 → 立即恢复 `0x00`。
     - bit1(Button Switch)同理,单独测。
     - 记录结论到 README 的 HID 报告表(报告 6 含义改为实测结果)。
  2. 若确认 bit0=Surface(触控板)开关:
     - `haptic.py` 增加 `set_surface_switch(path, on)` /
       `get_surface_switch(path)`,封装报告 6 读写。
     - CLI:`z13-touchpad-apply --touchpad on|off`。
     - GUI:设置页加一行"触控板开关"开关。
  3. **不持久化**:开机自动应用里不要包含"禁用"状态(防止登录即锁死触控板);
     文档注明。
- **验证**:开关来回一次,每次触摸确认状态,结束恢复启用。
- **风险**:高——语义未验证;若写入被固件忽略,记录阴性结果并停在此功能,
  不强行继续。

### 功能 3:Win8 PTP blob(报告 7)探索

- **目标**:系统扫描 256 字节 blob,找出影响触控板行为的偏移(如 tap 区域、
  掌压抑制),产出发现文档;若发现有用开关,接入 GUI/CLI。
- **依据**:blob 当前全零;内核 `hid-multitouch` / `hhd` 将其视为 Win8 合规
  blob。wiki 在此处无进展。
- **涉及**:新建 `blob-sweep.py`(实验脚本)、`docs/blob-findings.md`、
  可能改 `haptic.py`/`gui.py`。
- **步骤**:
  1. 新建 `blob-sweep.py`:读报告 7 → 存快照 → 按偏移逐字节置 `0xFF`
     (或预设候选值)→ 写回 → 提示用户做行为测试(点击/拖动/双指)→
     记录 → 恢复。每轮只改一个字节,写完立即回读确认。
  2. 安全要求:复用 `feature-probe.py` 的快照机制(改共享快照文件或
     各自独立,以不互相覆盖为准);每轮结束恢复,脚本退出前保证还原。
  3. 用户实测(需图形会话)后,把结果写进 `docs/blob-findings.md`。
  4. 若发现可控行为偏移:在 `haptic.py` 加命名访问函数,CLI/GUI 接入。
- **验证**:每字节读写回读一致;快照恢复后 `--dump` 与改前一致。
- **风险**:高(未知字段可能改变触控行为,重启可重置)。**只允许
  单字节、快照、逐个测**;发现异常立即恢复。

---

## Phase 2 — 同机型硬件怪癖套件

### 功能 4:OLED 唤醒亮度恢复 + 非线性换算

- **目标**:
  a) 显示器重新启用(合盖/唤醒)后,把亮度恢复到内核上报值(wiki: 显示器
     会在内核仍报旧值的情况下以默认亮度亮起);
  b) 提供 Linux/Windows 亮度换算(30% Linux ≈ 75% Windows)。
- **涉及**:新建 `brightness.py`、`z13-brightness-apply`(CLI)、改
  `resume-watch.py`(唤醒时一并恢复亮度)、README。
- **步骤**:
  1. 探测背光设备:`/sys/class/backlight/amdgpu_bl*/brightness`(可能多个,
     选非 LED 面板那个;用 `type` 属性判断 `firmware`/`platform`/`raw`)。
  2. 确认写权限:当前用户能否写 `brightness`?不能则给出方案
     (udev 规则或 polkit 规则,写成文档;需要 root 的部分标注由用户执行,
     **不要**自行安装系统级规则)。
  3. `brightness.py`(stdlib):`get_brightness()` 读内核值、
     `set_brightness(v)` 写(失败给明确错误)、`to_windows(lv)` /
     `to_linux(wv)` 非线性换算(曲线常数放模块顶部,来源 wiki:30%≈75%,
     补充 2-3 个锚点,其余线性插值,注明是近似)。
  4. `z13-brightness-apply`:`--get` / `--set N` / `--to-windows N` /
     `--to-linux N`。
  5. `resume-watch.py`:唤醒回调里先等 1-2 秒(等面板稳定),再调用亮度
     恢复(读当前内核值重写即可,因为内核值一直是用户上次设的)。
- **验证**:`--get` 有值;`--set` 前后回读一致(若权限不足,只验证错误
  路径与换算函数);换算函数单元测试。
- **风险**:中——背光写权限可能受限;`amdgpu_bl` 设备名因机而异,
  探测逻辑要容错;恢复亮度**永远写内核上报值**,不写死硬编码值。

### 功能 5:摄像头 IR/RGB 选择修复

- **目标**:web 应用总选 IR 相机导致失败——提供检测与切换引导。
- **依据**:wiki "web applications have a tendency to pick the IR camera first"。
- **涉及**:新建 `z13-camera-tool`(CLI)、README。
- **步骤**:
  1. 探测:`/sys/class/video4linux/video*/name` + `v4l2-ctl --list-devices`
     (若存在),标注哪个是 IR(名称含 IR / 04f2:b78c),哪个是 RGB。
  2. CLI 输出设备清单 + 推荐配置:
     - 若有 `v4l2-ctl`/`gst` 可设置的应用级默认,给出命令;
     - 否则输出"在应用的相机选择里选 RGB"的指引;
     - 评估 `PIPEWIRE_NODE` 提示或 portal 覆盖是否可行,可行则提供。
  3. **不做** v4l2loopback 内核模块方案(需 root,改动大),除非用户要求。
- **验证**:无头运行列出本机相机,IR/RGB 标记正确。
- **风险**:低-中——方案深度有限,交付物主要是检测+引导。

### 功能 6:GPU reset 监控通知

- **目标**:amdgpu 发生 reset 时通知用户并记录日志。
- **依据**:wiki "GPU has a tendency to reset under Chrome/Electron"。
- **涉及**:新建 `gpu-reset-watch.py`、可选 systemd 用户服务、README。
- **步骤**:
  1. 检测通道:`journalctl -k -f`(需 `adm`/`systemd-journal` 组权限,先验证)
     或读 `/dev/kmsg`;过滤 `amdgpu.*reset`(含 `GPU reset` / `ras` 关键字)。
  2. 事件发生时:`notify-send` 弹通知(若可用)+ 追加时间戳到
     `~/.config/z13-g2-tools/gpu-resets.log`。
  3. 提供安装 systemd 用户服务的说明(仿 `gui.set_autostart` 的模式,
     或文档命令)。
- **验证**:`journalctl -k -g "reset"` 有输出则手动喂一条事件测试;
  无历史事件则验证"过滤+日志"逻辑(单元测试)。
- **风险**:低——纯旁路监控,不写任何设备。

### 功能 7:Untested 硬件探测

- **目标**:探测指纹(06cb:0123)、TPM、IR 相机的存在与可用性,结果
  写进本地文档(自用,不回传任何地方)。
- **涉及**:新建 `hw-probe.py`、README、`docs/hardware-findings.md`。
- **步骤**:
  1. 指纹:`/sys/bus/usb/devices/*/idVendor+idProduct == 06cb:0123`,
     只报告存在性与驱动绑定(`lsusb` 存在时用;`/sys/bus/usb` 信息足够)。
  2. TPM:`/sys/class/tpm/tpm0/tpm_version_major`(有 2.0/1.2),
     `/dev/tpm0` 是否存在。
  3. IR 相机:video4linux 名称含 IR;与 RGB 相机并列输出。
  4. 表格输出"状态: 工作/未测试/缺失"。
- **验证**:本机运行,输出合理(指纹 06cb:0123 若出现在 lsusb 则标
  "可检测到";TPM 应有 tpm0),与 wiki 硬件表对照做参考。
- **风险**:低。

---

## Phase 3 — 工具层增强

### 功能 8:预设 profile(轻触 / 标准 / 重按)

- **目标**:一键切换三套力度预设。
- **涉及**:`z13-touchpad-apply`、`gui.py`、`haptic.py`(常量)、README。
- **步骤**:
  1. 在 `haptic.py` 或新常量区定义 `PROFILES`:
     - 轻触:click 110g / release 72g(或 65% 联动值)
     - 标准:164g / 108g(设备默认)
     - 重按:240g / 156g
     (数值可再议,要求:release 取 click 的 65% 附近,保持与联动逻辑一致)
  2. CLI:`z13-touchpad-apply --profile <light|standard|heavy>` —— 写入
     主区+释放,并保存配置。
  3. GUI:设置页"点击力度"组顶部加下拉框(Adw.ComboRow 或 PreferencesGroup
     里放 `Adw.EntryRow`+按钮),选中即应用。
- **验证**:三个 profile 各应用一次,`--show` 回读一致,最后恢复标准。
- **风险**:低。

### 功能 9:设备信息页

- **目标**:GUI 新增"设备"页显示 HID_ID / HID_NAME / HID_PHYS / 驱动 / 内核模块。
- **涉及**:`haptic.py`(新函数)、`gui.py`、README。
- **步骤**:
  1. `haptic.py` 增加 `device_info(path=None)` → dict:
     从 `/sys/class/hidraw/hidrawN/device/uevent` 解析 `HID_ID`、`HID_NAME`、
     `HID_PHYS`、`DRIVER`;从 `/sys/class/hidraw/hidrawN/device/../`
     找不到驱动模块名就留空,不硬编码。
  2. `gui.py`:新增第三个页面"设备"(`self.stack.add_titled(...)`),只读
     ActionRow 逐项展示。
- **验证**:无头打印 dict 与 uevent 一致。
- **风险**:低。

### 功能 10:CLI 增补

- **目标**:`--list-devices`、`--watch-test`、`--reg`。
- **涉及**:`z13-touchpad-apply`、`resume-watch.py`。
- **步骤**:
  1. `--list-devices`:列出全部 `/dev/hidraw*`,标注哪个匹配触控板
     (复用 `find_device` 的匹配逻辑,输出 HID_NAME 与路径)。
  2. `--watch-test`:自测唤醒监听链路——给 `resume-watch.py` 加
     `--self-test` 分支直接触发一次 `run_apply`(不要用 busctl 伪造
     信号:GDBus 按发送方匹配,伪造信号不会命中订阅;实测验证过)。
  3. `--reg <0xXXXX> <value|read>`:通用寄存器读写(调试用,值按 raw,
     CLI 内部换算函数同样适用)。
- **验证**:逐个无头运行;`--watch-test` 后回读确认 apply 已执行
  (改一个寄存器再测,测完恢复)。
- **风险**:低。

### 功能 11:GNOME 快捷设置集成

- **目标**:GNOME 快速设置(Quick Settings)里出现触控板力度/触感开关。
- **涉及**:新建 `extensions/` 目录(gnome-shell 扩展)、README。
- **步骤**:
  1. 新建 `extensions/z13-touchpad-quick@user/metadata.json` +
     `extension.js`:Quick Settings 项(目标 GNOME 45+/46,API 以目标版本
     为准,注明版本脆弱性),提供两个开关:
     - 触感强度开关(调 `z13-touchpad-apply --set-haptic 0|100` 或开关
       0x006E);
     - 触控板开关(功能 2 确认后接入;未确认则只放触感)。
  2. `extensions/install.sh`:复制到 `~/.local/share/gnome-shell/extensions/`
     + `gnome-extensions enable` 提示。
  3. **无法无头验证**——README 里写明"用户需在图形会话中
     `Alt+F2 r` 或重新登录后测试"。
- **风险**:中——shell 扩展 API 随 GNOME 版本漂移,交付时锁定版本并
  文档注明;保持扩展极简(只调 CLI,不在 JS 里重实现协议)。

---

## 收尾

1. 全量回归:py_compile、CLI `--get`/`--show`/`--dump`、服务状态。
2. 确认设备最终状态与配置一致(触感 75、点击 164g、释放 144g,或按
   用户最后调整的值为准)。
3. 更新 README 两份 + `docs/` 新文档,中英同步。
4. 向用户汇报:每项功能的完成度、验证方式、哪些需要用户图形会话实测
   (GUI 新控件、功能 2/3 的行为实验、功能 11 扩展)。
