# 硬件探测结果(ThinkPad Z13 Gen 2,本地自用)

- 生成时间:2026-07-31T23:28:55+08:00
- 由 `hw-probe.py` 生成,仅记录本机探测结果,不回传。

| 设备 | 状态 | 说明 |
|---|---|---|
| 指纹 06cb:0123 | 未测试 | 06cb:0123 已检测到(sysfs),但接口未绑定驱动,fprintd 可能不可用 |
| TPM | 工作 | TPM 2.0(tpm_version_major),/dev/tpm0 存在(当前用户无法打开:Permission denied;需 tss 组或 root) |
| IR 相机 | 工作 | 节点 /dev/video2、/dev/video3(Integrated IR Camera: Integrate) |
| RGB 相机 | 工作 | 节点 /dev/video0、/dev/video1(Integrated Camera: Integrated C) |

## 备注

- 状态含义:工作=已检测到且可用/已绑定;未测试=已检测到但缺驱动绑定或权限不足;缺失=未检测到。
- 指纹 06cb:0123(Synaptics):若接口未绑定驱动,fprintd 可能不可用,需用户确认内核模块/固件。
- /dev/tpm0 访问需 tss 组或 root(存在不等于当前用户可打开)。
- IR 相机 USB ID 04f2:b78c,RGB 相机 04f2:b78b;web 应用倾向优先选 IR,参考 z13-camera-tool。
- 与 Arch Wiki 硬件表对照仅作参考,本文件自用。
