// z13-touchpad-quick@user —— GNOME 快速设置里的触感强度开关。
//
// 只调用 z13-touchpad-apply CLI,不在 JS 里重实现任何 HID 协议:
//   开 → z13-touchpad-apply --set-haptic 100
//   关 → z13-touchpad-apply --set-haptic 0
//   状态 → z13-touchpad-apply --get(输出 0-100 整数,>0 视为开)
//
// 目标版本:GNOME 50.2(本机实测)。metadata.json 声明 45-50,但只有
// 50.2 验证过;更早版本用 addExternalIndicator 的回退分支,可能不显示。
//
// 注:GNOME 45+ 的 QuickToggle 在 toggleMode=false(默认)时点击不会自动
// 翻转 checked,因此这里手动管理 checked,并且只在子进程退出、--get 回读
// 完成后才 set_active,避免在设备写入结束前改 UI。

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import * as ExtensionUtils from 'resource:///org/gnome/shell/misc/extensionUtils.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

// 开关"开"时写入的触感强度(0-100)。想用 80 等其它值,改这里即可
//(CLI 的 --set-haptic 也接受 0-100 任意值,但本开关只有"开/关"两态)。
const ON_INTENSITY = 100;

// 找 z13-touchpad-apply,按优先级:
//   1) install.sh 安装时写进扩展目录的 cli-path 文件(绝对路径)
//   2) PATH
//   3) ~/.local/bin、~/bin
// 都找不到返回 null(扩展照常加载,开关点击无效并记日志)。
function findCliPath() {
    const ext = ExtensionUtils.getCurrentExtension();
    try {
        const file = Gio.File.new_for_path(`${ext.path}/cli-path`);
        if (file.query_exists(null)) {
            const [, contents] = file.load_contents(null);
            const path = new TextDecoder().decode(contents).trim();
            if (path && GLib.file_test(path, GLib.FileTest.IS_EXECUTABLE))
                return path;
        }
    } catch (e) {
        log(`z13-touchpad-quick: 读取 cli-path 失败,继续用其它方式找 CLI: ${e}`);
    }

    const inPath = GLib.find_program_in_path('z13-touchpad-apply');
    if (inPath)
        return inPath;

    for (const dir of [`${GLib.get_home_dir()}/.local/bin`, `${GLib.get_home_dir()}/bin`]) {
        const candidate = `${dir}/z13-touchpad-apply`;
        if (GLib.file_test(candidate, GLib.FileTest.IS_EXECUTABLE))
            return candidate;
    }
    return null;
}

export default class Z13TouchpadQuickExtension {
    enable() {
        this._enabled = true;
        this._busy = false;
        this._running = new Set();
        this._cliPath = findCliPath();
        if (!this._cliPath)
            log('z13-touchpad-quick: 找不到 z13-touchpad-apply,开关将无效。' +
                '可把仓库的 z13-touchpad-apply 加入 PATH,或重跑 install.sh 重新写入 cli-path。');

        this._toggle = new QuickSettings.QuickToggle({
            title: '触感强度',
            iconName: 'input-touchpad-symbolic',
            // 默认 toggleMode=false:点击不自动翻转,状态由 --get 回读决定
        });
        this._toggle.checked = false;
        this._toggle.connect('clicked', () => this._onToggleClicked());

        this._indicator = new QuickSettings.SystemIndicator();
        const items = this._indicator.quickSettingsItems ??
            this._indicator._quickSettingsItems;
        items.push(this._toggle);

        const qs = Main.panel.statusArea.quickSettings;
        if (qs.addExternalIndicator)
            qs.addExternalIndicator(this._indicator);
        else
            qs._addQuickSettingsItems(items, 0);

        this._refreshStatus();
    }

    disable() {
        this._enabled = false;
        for (const proc of this._running)
            proc.force_exit();
        this._running = null;
        this._toggle?.destroy();
        this._toggle = null;
        this._indicator?.destroy();
        this._indicator = null;
    }

    // 点击:目标态 = 当前 checked 取反;写入期间忽略后续点击(_busy)。
    _onToggleClicked() {
        if (this._busy || !this._cliPath)
            return;
        const target = this._toggle.checked ? 0 : ON_INTENSITY;
        this._busy = true;
        this._runCli(['--set-haptic', String(target)], result => {
            if (!result.success)
                log(`z13-touchpad-quick: 设置触感强度 ${target} 失败: ${result.stderr.trim()}`);
            // 无论成败都以设备真实状态为准回读,回读完成才解锁 UI
            this._refreshStatus(() => {
                this._busy = false;
            });
        });
    }

    // 用 --get 回读并同步 toggle 的 active 状态(子进程退出后才 set_active)。
    _refreshStatus(onDone) {
        this._runCli(['--get'], result => {
            if (result.success) {
                const value = parseInt(result.stdout.trim(), 10);
                if (!Number.isNaN(value))
                    this._toggle.checked = value > 0;
            } else {
                log(`z13-touchpad-quick: 读取状态失败: ${result.stderr.trim()}`);
            }
            onDone?.();
        });
    }

    // 异步跑一次 CLI:args 为参数数组;callback({success, stdout, stderr})
    _runCli(args, callback) {
        if (!this._cliPath) {
            callback?.({success: false, stdout: null, stderr: 'z13-touchpad-apply 未找到'});
            return;
        }
        const proc = new Gio.Subprocess({
            argv: [this._cliPath, ...args],
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        this._running.add(proc);
        proc.init(null);
        proc.communicate_utf8_async(null, null, (subprocess, res) => {
            // disable() 之后回调仍可能触发:先判 _enabled,再碰 _running
            if (!this._enabled)
                return;
            this._running.delete(subprocess);
            try {
                const [ok, stdout, stderr] = subprocess.communicate_utf8_finish(res);
                callback?.({success: ok, stdout: stdout ?? '', stderr: stderr ?? ''});
            } catch (e) {
                log(`z13-touchpad-quick: 调用 '${args.join(' ')}' 出错: ${e}`);
                callback?.({success: false, stdout: null, stderr: String(e)});
            }
        });
    }
}
