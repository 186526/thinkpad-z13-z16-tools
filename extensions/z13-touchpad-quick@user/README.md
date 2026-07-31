# Z13 触控板触感开关(GNOME 扩展)

在 GNOME 快速设置(Quick Settings)面板里开关 ThinkPad Z13/Z16 Gen 2
触觉触控板的**触感强度**。

- 只调用 `z13-touchpad-apply` CLI,**不在 JS 里重实现任何 HID 协议**。
- 开 → `z13-touchpad-apply --set-haptic 100`
- 关 → `z13-touchpad-apply --set-haptic 0`
- 显示状态 → `z13-touchpad-apply --get` 回读(0-100 整数,`>0` 视为开),
  在子进程退出后才更新开关状态,避免在设备写入结束前改 UI。

> 说明:开关"开"会把强度设为 100(默认),这会覆盖 GUI 里设的其它强度值;
> 想用 80 等其它值,改 `extension.js` 顶部的 `ON_INTENSITY` 常量即可。

## 前置条件

- 仓库的 `z13-touchpad-apply` 可用(其依赖的 `/dev/hidrawN` 权限与
  项目其它工具一致,需在 `plugdev` 组或获得 uaccess 授权)。
- 扩展按以下顺序找 CLI:`install.sh` 写入的 `cli-path` 文件 →
  `PATH` → `~/.local/bin`、`~/bin`。

## 安装与启用

```bash
# 在仓库的 extensions/ 目录下
./install.sh
# 或:bash install.sh

# 启用(或使用"扩展管理器 / Extensions"应用)
gnome-extensions enable z13-touchpad-quick@user
```

`install.sh` 幂等:已存在则整体覆盖重装;它会把仓库内
`z13-touchpad-apply` 的绝对路径写进已安装目录的 `cli-path` 文件。

## 测试

本扩展**无法无头验证**,请在图形会话中:

1. 重新加载 shell:`Alt+F2` → 输入 `r` → 回车(或注销重新登录)。
2. 点击右上角状态栏打开快速设置面板,看到"触感强度"开关(图标为触控板)。
3. 点击开关:开 → 触感明显变强;关 → 无触感。开关状态与 `--get` 一致
   (可在终端用 `z13-touchpad-apply --get` 对比)。
4. 排查:若开关不显示,查看 shell 日志
   `journalctl --user -b | grep z13-touchpad-quick`;若开关无效,
   检查 `cli-path` 与设备权限(`z13-touchpad-apply --get` 手动跑一下)。

## 版本脆弱性(重要)

- 本扩展在 **GNOME 50.2**(本机)验证;**shell-version 声明了 45-50,
  但 45-49 未验证**。
- 依赖的 API:`ui/quickSettings.js` 的 `QuickToggle` / `SystemIndicator`
  (及其 `quickSettingsItems` 数组)与 `Main.panel.statusArea.quickSettings
  .addExternalIndicator()`(50.x 的公开方法;更早版本走 `_addQuickSettingsItems`
  回退分支)。GNOME 大版本升级后若开关不显示,先检查 API 是否变化并更新
  `metadata.json` 的 `shell-version`。
- 已知差异:GNOME 45 时代的 `QuickSettingsMenu(带 header 的菜单项)` 类在
  50 中已不存在,故本扩展采用 `SystemIndicator` + `QuickToggle` 的写法。

## 限制

- 只做"触感强度"开关一个 toggle。功能 2 的"触控板开关"(HID 报告 6)
  语义尚未确认,确认前不接入。
- 状态只在启用时与每次点击后刷新;GUI 里改动强度后,开关状态到下次
  点击或重载扩展时才会同步。
