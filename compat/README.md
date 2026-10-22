# 可选宿主补丁

以下补丁针对 **octop-gateway 1.0.3 / Octop 1.0.2b9**，不由插件自动修改或安装到宿主。

- `gateway-enqueue-receipts.patch`：同事件循环入队立即返回 `True`/`False`，跨线程调用仍兼容原先的异步行为。插件收到明确拒绝后保留消息并重试。
- `octop-runtime-diagnostics.patch`：在现有频道状态 API 的 `runtime` 中附加 `diagnostics`，包含 WS、分发、轮询、提示队列及数据库统计，不包含 Token、消息正文和发送者 UID。

应用前停止宿主、备份相应源码，并先检查补丁。对宿主源码根目录或虚拟环境的 `site-packages` 目录执行：

```sh
patch --dry-run -p1 -d /宿主源码根目录 -i /插件绝对路径/compat/gateway-enqueue-receipts.patch
patch -p1 -d /宿主源码根目录 -i /插件绝对路径/compat/gateway-enqueue-receipts.patch
patch --dry-run -p1 -d /宿主源码根目录 -i /插件绝对路径/compat/octop-runtime-diagnostics.patch
patch -p1 -d /宿主源码根目录 -i /插件绝对路径/compat/octop-runtime-diagnostics.patch
```

其他版本先对照上游实现，不要强制套用。CI 同时测试未打补丁的旧接口与已打补丁的接口。

未打入队补丁时，插件最多保留一条尚未被 gateway worker 取走 的消息，避免自身填满频道队列。旧接口的 `None` 返回无法证明入队成功：如果还有外部代码向同一队列写入、且队列拒绝这次消息，插件会报告交接停滞，并等待人工核查；不会因超时自动重复执行。需要覆盖这种情况时应用入队回执补丁。

这两份补丁不提供工具权限隔离。非所有者工具白名单仍须由宿主在工具执行端实施。
