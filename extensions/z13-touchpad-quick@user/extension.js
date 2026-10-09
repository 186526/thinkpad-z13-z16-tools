// z13-touchpad-quick@user —— GNOME 快速设置里的触感强度五档调节。
//
// 只调用 z13-touchpad-apply CLI,不在 JS 里重实现任何 HID 协议:
//   写档位 → z13-touchpad-apply --set-haptic 0/25/50/75/100
//   读状态 → z13-touchpad-apply --get(输出 0-100 整数)
//
// 目标版本:GNOME 50.2(本机实测)。metadata.json 声明 45-50,但只有
// 50.2 验证过;更早版本用 addExternalIndicator 的回退分支,可能不显示。
//
// 交互(参照 GNOME 内置的"键盘"亮度项 status/backlight.js 的
// QuickMenuToggle + 离散档位按钮模式):
//   - 点整行 = 开关:关 ↔ 上次非零档位;
//   - 点右侧菜单按钮 = 弹出 5 个档位按钮(0/25/50/75/100,与固件吸附
//     档一致),点选即写,当前档位高亮。

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

// 五档吸附值(与固件 feature 报告 11 的 0/25/50/75/100 一致)。
const LEVELS = [0, 25, 50, 75, 100];
const LEVEL_LABELS = ['关', '25%', '50%', '75%', '100%'];

// 找 z13-touchpad-apply,按优先级:
//   1) install.sh 安装时写进扩展目录的 cli-path 文件(绝对路径)
//   2) PATH
//   3) ~/.local/bin、~/bin
// 都找不到返回 null(扩展照常加载,开关点击无效并记日志)。
// extPath 为扩展安装目录(GNOME 45+ 用 Extension 基类的 this.path,
// getCurrentExtension() 在 50 已移除)。
function findCliPath(extPath) {
    try {
        const file = Gio.File.new_for_path(`${extPath}/cli-path`);
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

// 菜单里的五档按钮组(参照内置 DiscreteItem):每个档位一个竖排
// "图标按钮 + 文字",点选写入 value(0-100),当前档位 checked 高亮。
const HapticLevelsItem = GObject.registerClass({
    Properties: {
        'value': GObject.ParamSpec.int(
            'value', null, null,
            GObject.ParamFlags.READWRITE,
            0, 100, 0),
    },
}, class HapticLevelsItem extends St.BoxLayout {
    _init() {
        super._init({
            style_class: 'popup-menu-item',
            reactive: true,
        });

        this._levelButtons = new Map();
        LEVELS.forEach((level, i) => this._addLevelButton(level, LEVEL_LABELS[i]));

        this.connect('notify::value', () => this._syncChecked());
        this._syncChecked();
    }

    _addLevelButton(level, labelText) {
        const box = new St.BoxLayout({
            style_class: 'keyboard-brightness-level',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
        });

        const label = new St.Label({
            text: labelText,
            x_align: Clutter.ActorAlign.CENTER,
        });

        box.button = new St.Button({
            styleClass: 'icon-button',
            canFocus: true,
            iconName: 'input-touchpad-symbolic',
            labelActor: label,
        });
        box.add_child(box.button);

        box.button.connect('clicked', () => {
            this.value = level;
        });

        box.add_child(label);

        this.add_child(box);
        this._levelButtons.set(level, box);
    }

    vfunc_key_press_event(event) {
        return global.focus_manager.navigate_from_event(event);
    }

    _syncChecked() {
        this._levelButtons.forEach((box, level) => {
            box.button.checked = level === this.value;
        });
    }
});

export default class Z13TouchpadQuickExtension extends Extension {
    enable() {
        this._enabled = true;
        this._busy = false;
        this._updatingUi = false;
        this._running = new Set();
        this._pendingValue = null;   // 排队等待写入的档位(0-100)
        this._lastApplied = null;    // 最近一次成功写入的档位,用于去重
        this._lastOnValue = 100;     // 行点击"开"时恢复的档位
        this._cliPath = findCliPath(this.path);
        if (!this._cliPath)
            log('z13-touchpad-quick: 找不到 z13-touchpad-apply,开关将无效。' +
                '可把仓库的 z13-touchpad-apply 加入 PATH,或重跑 install.sh 重新写入 cli-path。');

        this._toggle = new QuickSettings.QuickMenuToggle({
            title: '触感强度',
            iconName: 'input-touchpad-symbolic',
            menuButtonAccessibleName: '打开触感强度菜单',
        });

        this._levelItem = new HapticLevelsItem();
        this._toggle.menu.box.add_child(this._levelItem);

        // 行点击:关 ↔ 上次非零档位(checked 不随点击自动翻转,以设备状态为准)
        this._toggle.connect('clicked', () => this._onToggleClicked());
        this._levelItem.connect('notify::value', () => this._onLevelUserChanged());

        this._indicator = new QuickSettings.SystemIndicator();
        const items = this._indicator.quickSettingsItems ??
            this._indicator._quickSettingsItems;
        items.push(this._toggle);

        const qs = Main.panel.statusArea.quickSettings;
        if (qs.addExternalIndicator)
            qs.addExternalIndicator(this._indicator);
        else
            qs._addQuickSettingsItems(items, 0);

        this._syncFromDevice();
    }

    disable() {
        this._enabled = false;
        for (const proc of this._running)
            proc.force_exit();
        this._running = null;
        this._toggle?.destroy();
        this._toggle = null;
        this._levelItem = null;
        this._indicator?.destroy();
        this._indicator = null;
    }

    // 行点击:当前 checked 决定目标态。toggleMode=false 不自动翻转,
    // 点击后由写入/回读结果更新 checked。
    _onToggleClicked() {
        const target = this._toggle.checked ? 0 : (this._lastOnValue || 100);
        this._applyValue(target);
    }

    // 菜单档位按钮被点(notify::value 来自用户操作)。
    _onLevelUserChanged() {
        if (this._updatingUi)
            return;
        this._applyValue(this._levelItem.value);
    }

    // 应用一个档位:立即乐观更新 UI,排队写入,防重复子进程。
    _applyValue(value) {
        this._pendingValue = value;
        this._setUiValue(value);
        this._flushPending();
    }

    // 一次只跑一个子进程;期间新档位排队,写完后补写最新值。
    _flushPending() {
        if (this._busy || this._pendingValue == null)
            return;
        if (this._pendingValue === this._lastApplied) {
            this._pendingValue = null;
            return;
        }
        this._busy = true;
        const value = this._pendingValue;
        this._pendingValue = null;
        this._runCli(['--set-haptic', String(value)], result => {
            this._busy = false;
            if (!result.success) {
                log(`z13-touchpad-quick: 设置触感强度 ${value} 失败: ${result.stderr.trim()}`);
                this._syncFromDevice();   // 以设备真实状态为准回滚 UI
            } else {
                this._lastApplied = value;
                if (value > 0)
                    this._lastOnValue = value;
            }
            this._flushPending();
        });
    }

    // 程序化同步 UI(checked + 档位高亮),抑制 notify::value 回环。
    _setUiValue(value) {
        this._updatingUi = true;
        this._levelItem.value = value;
        this._toggle.checked = value > 0;
        this._updatingUi = false;
    }

    // 用 --get 回读并同步 UI(子进程退出后才改值)。
    _syncFromDevice() {
        this._runCli(['--get'], result => {
            if (result.success) {
                const value = parseInt(result.stdout.trim(), 10);
                if (!Number.isNaN(value)) {
                    const level = this._nearestLevel(value);
                    this._lastApplied = level;
                    this._pendingValue = null;
                    if (level > 0)
                        this._lastOnValue = level;
                    this._setUiValue(level);
                }
            } else {
                log(`z13-touchpad-quick: 读取状态失败: ${result.stderr.trim()}`);
            }
        });
    }

    _nearestLevel(value) {
        let best = LEVELS[0];
        for (const level of LEVELS) {
            if (Math.abs(value - level) <= Math.abs(value - best))
                best = level;
        }
        return best;
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
