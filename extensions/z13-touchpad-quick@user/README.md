# Z13 触控板触感强度五档调节(GNOME 扩展)

在 GNOME 快速设置(Quick Settings)面板里调节 ThinkPad Z13/Z16 Gen 2
触觉触控板的**触感强度**,交互参照 GNOME 内置的"键盘"亮度项
(`status/backlight.js` 的 `QuickMenuToggle` + 离散档位按钮模式):

- 点整行 = 开关:关 ↔ 上次非零档位;
- 点右侧菜单按钮 = 弹出 **5 个档位按钮**(0/25/50/75/100,与固件吸附档
  一致),当前档位高亮,点选即写。

- 只调用 `z13-touchpad-apply` CLI,**不在 JS 里重实现任何 HID 协议**。
- 写档位 → `z13-touchpad-apply --set-haptic N`(N ∈ 0/25/50/75/100)
- 显示状态 → `z13-touchpad-apply --get` 回读(0-100 整数),在子进程
  退出后才更新 UI,避免在设备写入结束前改界面。

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
```

`install.sh` 幂等:已存在则整体覆盖重装;它把仓库内
`z13-touchpad-apply` 的绝对路径写进已安装目录的 `cli-path` 文件,并把
UUID 追加进 GNOME 的启用列表(`org.gnome.shell enabled-extensions`,
保留其它已启用扩展)——**下次登录/重启后自动启用,无需手动操作**。

若当前 shell 已感知该扩展(扩展目录先于 shell 启动存在),`install.sh`
会立即启用;若是刚装的,shell 要到下次登录/重启才会扫描到新扩展。
手动启用(或使用"扩展管理器 / Extensions"应用)仍可用:

```bash
gnome-extensions enable z13-touchpad-quick@user
```

> 为什么 web 上"从 extensions.gnome.org 一键安装"能立即生效:浏览器插件
> 通过 D-Bus(`InstallRemoteExtension`)让 shell 自己下载并在运行时加载,
> 那是 shell 的联网安装路径,本地扩展不适用。本地装的扩展 shell 只在
> 启动时扫描目录;且 GJS 会缓存已导入的扩展模块(注释 "Extensions can
> only be imported once"),改代码后必须注销重新登录才生效
> (`org.gnome.Shell.Eval` 在 50 需 `--unsafe-mode` 启动,`ReloadExtension`
> D-Bus 已弃用,均不可用)。

## 测试

本扩展**无法无头验证**,请在图形会话中:

1. 注销重新登录(或重启),让 shell 重新扫描扩展目录
   (`Alt+F2` → `r` 的重载命令在 GNOME 42+ 已移除,不可用)。
2. 点击右上角状态栏打开快速设置面板,看到"触感强度"项(图标为触控板)。
3. 点击整行:开 → 触感变强;关 → 无触感。
4. 点击右侧箭头按钮,菜单里出现 5 个档位按钮(关/25%/50%/75%/100%):
   当前档位高亮,点选后立即生效,与 `--get` 回读一致(可在终端用
   `z13-touchpad-apply --get` 对比)。
5. 排查:若该项不显示,查看 shell 日志
   `journalctl --user -b | grep z13-touchpad-quick`;若点击无效,
   检查 `cli-path` 与设备权限(`z13-touchpad-apply --get` 手动跑一下)。

## 版本脆弱性(重要)

- 本扩展在 **GNOME 50.2**(本机)验证;**shell-version 声明了 45-50,
  但 45-49 未验证**。
- 依赖的 API:`ui/quickSettings.js` 的 `QuickMenuToggle` / `SystemIndicator`
  (及其 `quickSettingsItems` 数组)与 `Main.panel.statusArea.quickSettings
  .addExternalIndicator()`(50.x 的公开方法;更早版本走 `_addQuickSettingsItems`
  回退分支)。GNOME 大版本升级后若该项不显示,先检查 API 是否变化并更新
  `metadata.json` 的 `shell-version`。
- 已知差异:GNOME 45 时代的 `QuickSettingsMenu(带 header 的菜单项)` 类在
  50 中已不存在,故本扩展采用 `SystemIndicator` + `QuickMenuToggle` 的写法。

## 限制

- 只做"触感强度"调节一项。功能 2 的"触控板开关"(HID 报告 6)语义
  尚未确认,确认前不接入。
- 状态只在启用时与每次操作后刷新;GUI 里改动强度后,该项状态到下次
  点击或重载扩展时才会同步。
