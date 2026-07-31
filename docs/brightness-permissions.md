# 背光写权限方案(Z13 Gen 2,本地自用)

## 现状(本机实测)

- 背光设备:`/sys/class/backlight/amdgpu_bl1`(type=`raw`,唯一非 led 面板背光)。
- `brightness` 文件权限:`-rw-r--r-- root:root`,普通用户**不可写**。
- 因此 `z13-brightness-apply --set N` 会失败,stderr 提示中文错误,并指向
  本文档。
- 读取不受影响(`--get` 正常);GNOME 的亮度快捷键/设置走 logind
  D-Bus(`org.freedesktop.login1.Session.SetBrightness`),登录会话本身就
  有权限,不受此限制——本方案只解决 CLI 直写 sysfs 的授权。

## 先确认现状(可选)

```bash
ls -l /sys/class/backlight/*/brightness   # 看权限与属组
groups                                     # 当前用户是否在 video/plugdev 组
./z13-brightness-apply --get              # 读取不受权限影响,应先能正常工作
```

- 若 `brightness` 已可写(如 udev 规则已生效),`--set` 应直接成功,无需再配置。
- 若背光设备名不是 `amdgpu_bl1`(换机/换面板),可用
  `./z13-brightness-apply --device /sys/class/backlight/<dev>` 显式指定;
  探测逻辑本身会优先选"非 led 类型"的面板背光。

## 方案 A(推荐):udev 规则,把写权限给 video 组

新建 `/etc/udev/rules.d/90-backlight-write.rules`:

```
ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="amdgpu_bl1", \
    RUN+="/usr/bin/chgrp video /sys/class/backlight/%k/brightness", \
    RUN+="/usr/bin/chmod g+w /sys/class/backlight/%k/brightness"
```

需要 root 的步骤(由用户执行):

```bash
sudo tee /etc/udev/rules.d/90-backlight-write.rules >/dev/null <<'EOF'
ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="amdgpu_bl1", RUN+="/usr/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/usr/bin/chmod g+w /sys/class/backlight/%k/brightness"
EOF
# 立即生效(或重启/重新插拔面板后自动生效)
sudo chgrp video /sys/class/backlight/amdgpu_bl1/brightness
sudo chmod g+w /sys/class/backlight/amdgpu_bl1/brightness
```

当前用户已在 `video` 组,规则生效后即可直接 `z13-brightness-apply --set`。
提示:规则用 `%k` 匹配设备名,`KERNEL=="amdgpu_bl1"` 可换成
`KERNEL=="amdgpu_bl*"` 以覆盖 amdgpu 面板(本机只有一个,写死亦可)。

生效验证:

```bash
ls -l /sys/class/backlight/amdgpu_bl1/brightness   # 应为 -rw-rw-r-- root video
./z13-brightness-apply --set 50                     # 应输出"亮度已设为 50%…"
./z13-brightness-apply --get                        # 回读确认
```

## 方案 B:polkit 规则(仅对走 logind D-Bus 的客户端有效)

`/etc/polkit-1/rules.d/50-backlight.rules`:

```
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.login1.set-brightness" &&
        subject.isInGroup("video")) {
        return polkit.Result.YES;
    }
});
```

需要 root 的步骤(由用户执行):把上面内容写入上述文件后
`sudo systemctl restart polkit`(或注销重新登录)。

注意:本仓库的 `z13-brightness-apply` 直接写 sysfs,不经过 logind,
**polkit 规则对它无效**;它只对通过 D-Bus 调 `SetBrightness` 的客户端
(如 GNOME 设置/自定义脚本)生效。CLI 场景请用方案 A。

## 其他替代(不推荐)

- `sudo z13-brightness-apply --set N`:每次要输密码,不做。
- 让 `brightness` 文件 `root:root 0666`:所有用户可写,过宽,不做。
- 关掉内核面板亮度保护(如 `amdgpu.abmlevel`):与本问题无关,不做。
