# Windows 本地测试包：1.0.4rc1

这是修复 `探测失败: No module named 'fcntl'` 的本地测试版本，未推送或发布到 GitHub，正式 1.0.3 安装包保持不变。

## 修复范围

保存 Octo 通道配置时，宿主会执行一次启动/停止探测。旧版在打开 SQLite 状态库时无条件导入 Unix 专用 `fcntl`，因此 Windows 可以安装插件，但保存探测失败。

本版 Windows 使用 Python 标准库 `msvcrt` 的非阻塞字节锁，Linux/macOS 继续使用 `flock`，不增加依赖。相同状态库仍禁止两个消费者同时打开；关闭、初始化失败和进程退出都会释放文件句柄及锁。状态库格式未改变。

同时修复了 Windows 只读诊断的文件 URI，并增加 Windows CI 配置。本地 macOS 上的 Windows 适配器模拟测试不能代替 Windows 实机验证；当前没有声称 Windows 端到端测试通过。

## 安装

1. 保留原 Bot Token、API URL 与现有状态目录；测试前停止旧 Octo 通道。
2. 在 Octop 管理页面上传 `octop-channel-octo-1.0.4rc1.zip`，覆盖同 ID 插件。
3. 重新加载插件或重启 Octop，确认插件版本为 `1.0.4rc1`，避免进程继续使用旧模块。
4. 保持原 Token/API URL，打开 Octo 通道配置并保存。

## 需要反馈的验证结果

- **配置保存**：探测成功，不再出现 `No module named 'fcntl'`；停止通道后再次保存/启动也正常。
- **收发消息**：私聊一条文本；群聊直接 @机器人一条文本，确认能收到回复。
- **重启恢复**：重启 Octop 后能再次启动通道并收发消息，已有状态无需删除。
- **异常占用**：如果提示 `Octo state already owned by another process`，先确认没有旧进程或第二个消费者正在运行，不要删除锁文件绕过保护。

如仍报错，请提供 Windows 版本、Python 版本、Octop 版本，以及从点击保存到失败期间的相关错误堆栈。日志中的 Token 和消息正文应先移除。

Windows 状态目录由当前账户的用户目录或 `OCTOP_HOME` 决定；使用自定义目录时应有写入权限。权限继承目录 ACL，不以 Unix 的 0600/0700 模式位作为 Windows 权限保证。
