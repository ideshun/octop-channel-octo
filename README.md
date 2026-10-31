# Octo IM 通道插件

中文 | [English](README.en.md)

将 Octo 私聊、群聊和社区话题接入 Octop，支持文本、图片、语音、视频和文件。通过 WebSocket 实时接收，REST 轮询补偿遗漏，并提供管理员限制、群白名单、群聊上下文、消息去重和确认重试。

**支持 Linux 和 macOS，已实测可用。**

**1.1.1** 修复群聊中机器人消息触发回复、@ 名称前缀误匹配，以及旧版 Gateway 配置兼容问题。群聊自动参与需安装 [`octop-social-trigger`](https://github.com/ideshun/octop-social-trigger)。

## 环境要求

Python 3.12+；Octop **1.0.2b9**、octop-gateway **1.0.3**、octop-harness **1.0.4**，或兼容后续版本。插件依赖由宿主管理器按 `plugin.yaml` 安装。

## 安装

正式版：[v1.1.1 发布说明](https://github.com/ideshun/octop-channel-octo/releases/tag/v1.1.1) · [下载插件 ZIP](https://github.com/ideshun/octop-channel-octo/releases/download/v1.1.1/octop-channel-octo-1.1.1.zip)。

推荐通过 Octop Web 管理界面安装，任选一种方式。

### 通过 URL 安装

打开 **管理 → 插件**，点击 **安装**，输入以下地址并确认：

```text
https://github.com/ideshun/octop-channel-octo/archive/refs/heads/main.zip
```

该地址跟随 main 分支更新。需要固定版本时，使用对应提交或发布版本的 ZIP。

### 上传 ZIP 安装

1. 从[仓库下载 ZIP](https://github.com/ideshun/octop-channel-octo/archive/refs/heads/main.zip)。
2. 打开 **管理 → 插件 → 从 ZIP 安装**。
3. 选择下载的 ZIP 并安装。

### 命令行安装

下载并解压源码，在 Octop 服务使用的运行环境中执行：

```sh
octop plugin install /插件目录的绝对路径
```

命令行不接受本地 ZIP 路径，请先解压。安装后确认插件已启用；若通道列表未出现 Octo，在 Web 管理页面重新加载插件或重启 Octop，再到专家通道页添加 Octo。CLI 的 `octop plugin reload` 只重载其自身进程，不会重载运行中的服务。

## 配置

### API URL

默认 `https://im.deepminer.com.cn/api`。自托管时填写对应服务的 API 基址，以 `/api` 结尾，不带查询参数或片段。

### Bot Token

在对应 Octo 服务的 BotFather 创建机器人并获取 Token。不要公开 Token 或提交到代码仓库。

### 回复与访问

- **管理员 UID 列表**：以逗号或空格分隔；Bot 所有者始终是管理员。
- **仅回复管理员**：默认关闭；开启后私聊和群聊均仅允许所有者及指定管理员触发回复。
- **仅所有者可使用命令**：默认关闭；开启后只有 Bot 所有者能发送 `/` 命令。其他成员（含管理员）可继续聊天；满足群聊触发规则的命令会收到简短提示，不执行、不占用对话。普通聊天的工具权限由宿主控制，见下方边界说明。
- **群聊白名单**：留空允许所有群聊；填写群或话题 ID，允许父群时也允许其话题。
- **仅回复 @机器人的群消息**：默认开启。普通群消息不触发回复，启用上下文时仍可作为上下文保留；私聊不受此开关影响，管理员限制仍生效。
- **群聊上下文条数**：每次最多附带的近期群消息数，默认 10，范围 0–100；0 关闭。自动参与还受策略插件的消息数和有效时间限制。此设置不清空专家已有会话历史。
- **忽略 @所有人**：默认关闭；开启后不将 @所有人 作为触发依据。

### 连接

- **显示正在输入**：默认开启。
- **心跳间隔**：默认 30 秒，范围 10–300 秒。
- **事件补偿轮询间隔**：默认 5 秒，范围 2–300 秒，与 WebSocket 同时工作。
- **允许私网／明文的精确主机名**：通常留空。连接私网或 HTTP 自托管服务时填写可信主机，以逗号或空格分隔，不含协议、端口或通配符。这会允许访问这些主机解析出的私网及回环地址，仅填写可信服务。

插件提供字段说明，配套宿主修复会在标签旁以“？”显示；“查看配置说明”指向[本仓库](https://github.com/ideshun/octop-channel-octo)。原版 b9 对插件描述、提示与默认值的展示存在限制，完整效果需要宿主修复及重新构建；插件不会修改宿主源码。见[界面兼容说明](docs/UX_PARITY.md)。

### 群聊自动参与

本通道已支持配合 [octop-social-trigger 群聊自动参与插件](https://github.com/ideshun/octop-social-trigger)，根据每个群和专家的设置选择性回复普通群消息；只需要 @ 触发时无需安装。需要此功能时，请按[插件安装说明](https://github.com/ideshun/octop-social-trigger/blob/develop/docs/install.md)另行安装 `octop-social-trigger`，并使用包含对应入站策略接口的 Octop 与 Gateway Fork。策略插件负责群范围、参与策略、频率限制和可选决策模型；本通道保留原有 @ 与命令权限。请保持“仅回复 @机器人的群消息”开启，由策略插件接管获准范围内的普通群消息；身份未知时不会自动参与。

### 工具权限边界

“仅所有者可使用命令”只限制 `/` 命令，不会限制普通聊天调用本机文件、终端或其他工具。消息提供来自平台所有者 UID 和管理员配置的 `sender_is_owner` / `sender_is_admin`，宿主必须据此在工具执行端实施权限限制，才能实现“其他成员仅聊天和查询公开资料”。管理员标识不等于所有者权限。插件逐条交付消息，禁止混合发送者批处理。

可选的入队回执与状态诊断宿主补丁见 [compat/README.md](compat/README.md)。未应用这些补丁时仍可使用原版宿主，插件使用有界交接并报告无法确认的交接停滞；不会自行修改生产宿主。

## 数据保存

自动在 Octop 数据目录下创建 SQLite 数据库，保存去重、确认重试和处理进度，无需填写状态目录。更新或轮换 Token 时保留数据；已有自定义 `state_dir` 继续使用原目录。详细边界见[运行说明](docs/OPERATIONS.md)。

## 更新与迁移

通过 Web 界面安装新版 ZIP，按提示处理同 ID 插件覆盖。命令行更新使用：

```sh
octop plugin install --force /新版插件目录的绝对路径
```

更新后重新加载插件或重启，保留配置及状态。

从旧通道迁移时，先备份并停止旧 Octo hook 及恢复／守护机制，再启用本插件。保留原通道 ID、`kind=octo` 和会话。旧状态不会自动导入新收件箱。**同一 Bot 不得同时运行两个消费者，包括不同机器。** 回滚时先停止新插件，再恢复旧通道。

## 验证与限制

平台可用性已实测；具体服务的 WS／REST／媒体互通、重放保留策略、负载与迁移仍需按部署环境验收。离线测试不替代端到端验收。

中断且结果不确定的任务标记为 `uncertain`，不自动重放；先核对外部副作用。`completed` 仅表示宿主处理路径返回，不保证外部操作恰好执行一次。

开发版识别 Octo 的 `type=99 / cmd=messageRevoke / param.message_id`，撤回通知与系统事件不会启动专家；撤回标记跨重启保留，待处理输入和后续输出会拦截，正在生成的对应回复会取消。已发出的内容或已经交给网络发送的请求无法自动撤销。协议依据见 [Octo SendRevoke](https://github.com/Mininglamp-OSS/octo-lib/blob/main/config/msg.go)。

暂不支持交互卡片、文档同步、Bot Tasks 和自动菜单。

## 开发

在具备目标宿主依赖的环境中运行：

```sh
python -m pytest
ruff check .
ruff format --check .
python scripts/build_zip.py
```

CI 固定目标宿主版本，覆盖 Linux/macOS、原版与带回执补丁的 gateway、进程中断恢复、突发消息和 ZIP 宿主加载。只读诊断及离线归档/恢复命令见[运行说明](docs/OPERATIONS.md)。

打包使用源码白名单，排除凭据、状态、测试和缓存。

## 许可证

Apache-2.0。见 `LICENSE`、`NOTICE` 和[来源记录](docs/PROVENANCE.md)。WebSocket 传输改编自 Mininglamp-OSS/openclaw-channel-octo，不内置第三方依赖。
