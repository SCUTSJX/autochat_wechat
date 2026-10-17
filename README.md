# ⚠️ 微信自动化控件：存在封禁风险，仅供技术展示

> **本项目不是微信官方产品，也不是腾讯授权的自动化接口。未经腾讯授权，通过第三方工具挂接、读取或控制微信，违反平台相关使用规则，存在账号被限制、暂停使用或封禁的风险。** 不保证任何使用频率、操作方式或运行环境下的账号安全。相关限制请阅读客户端内的现行微信协议及[腾讯服务协议](https://edu.tencent.com/agreement.html)。
>
> **本项目仅作技术展示与研究交流，不鼓励或授权任何违法违规用途。** 使用者应自行确认法律、平台规则和必要授权，并对自己的使用、修改、传播行为及其后果负责，不得用于骚扰、诈骗、非法营销或侵犯他人权益。
>
> **在适用法律允许的范围内，作者不对第三方使用本项目造成的账号损失、数据损失、费用、业务损失或纠纷承担责任；依法不得免除的责任除外。** 此声明不构成法律意见，也不能保证免除法定责任或避免法律纠纷。关于免责条款效力，可参阅[《民法典》第五百零六条](https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250312_1958939.html)。

<a id="overview"></a>

## autochat_wechat

面向 Ubuntu 图形桌面的微信 AI 自动回复工具，支持多聊天独立窗口、上下文回复，以及通过窗口截图理解图片和链接卡片。文字回复与图片识别可分别配置兼容 Chat Completions 的模型接口。

程序通过 Frida 接收运行中微信的新消息，通过 AT-SPI 识别聊天控件，并使用 X11 虚拟光标完成发送。图片识别使用会话窗口截图，不依赖微信图片缓存目录。

> **项目状态：测试阶段，许可证待确定。** 当前只适配特定 Linux 微信构建，并非适用于所有微信版本。首次使用请先在测试好友会话中完成初始化和收发验证。许可证与分发说明见 [DISTRIBUTION.md](DISTRIBUTION.md)。

<a id="contents"></a>

## 目录

- [项目简介](#overview)
- [功能](#features)
- [环境要求](#requirements) · [已实测版本](#tested-versions)
- [快速开始](#quick-start)
  - [1. 安装环境](#install)
  - [2. 启动微信](#launch-wechat)
  - [3. 配置 API 与群聊策略](#configure-api)
  - [4. 初始化账号](#initialize-account)
  - [5. 检查环境](#check)
  - [6. 启动与停止](#start)
- [日常使用与命令速查](#daily-use)
- [换号、重启与微信版本](#accounts)
- [配置文件与目录](#configuration)
- **[提示词在哪里修改](#prompts)**
  - [文字人设与系统提示词](#text-prompts)
  - [图片识别提示词](#vision-prompts)
  - [默认模板与高级任务规则](#default-prompts)
- [截图与上下文限制](#limitations)
- [常见问题](#faq)
- [隐私与分享](#privacy)
- [问题反馈与贡献](#contributing)
- [许可证](#license)

<a id="features"></a>

## 功能

- **多会话自动回复**：对已打开并唯一绑定的聊天独立窗口处理新消息。
- **上下文理解**：结合本次运行记录的文字、图片识别结果和引用关系生成回复。
- **截图识图**：定位会话窗口、截取画面，将识别结果加入对话上下文。
- **独立 API 配置**：文字和图片接口可使用不同服务商、模型和密钥，支持保存备用方案。
- **群聊策略**：可回复群内文字消息，或仅回复 @ 指定群昵称的消息。
- **账号初始化**：通过双向随机消息及桌面发送回读，为使用者生成本地账号校准。
- **环境检查与预览**：提供安装、检查和不调用模型、不发送消息的预览模式。

[↑ 返回目录](#contents)

<a id="requirements"></a>

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | **已实测：Ubuntu 20.04.6 LTS，64 位（x86_64 / amd64）**，图形桌面，具备 `apt-get` 和 `sudo` |
| 桌面会话 | **Xorg**；不支持 Wayland 下的完整工作流程 |
| 微信 | 已安装并登录的 Linux 微信 **4.1.13.26 / amd64**，且二进制指纹与适配文件匹配 |
| Python | **原可运行环境为 Python 3.8.10**；安装器使用系统 `python3` 创建虚拟环境，其他版本不保证适配 |
| 模型服务 | 文字接口支持 Chat Completions；图片接口还需支持图片输入 |
| 网络 | 安装时可访问 Ubuntu 软件源和 Python 包源；运行时可访问所配置的模型服务 |
| 使用账号 | 普通桌面用户；脚本在必要步骤自动请求 `sudo` |

微信的版本号相同也不代表构建相同。程序会核对 SHA256；指纹不匹配时会拒绝使用旧偏移。重复安装或重新初始化不能代替版本适配。

安装器不包含、不下载微信客户端。系统依赖包括 AT-SPI、GTK/GDK、D-Bus、xinput 及 X11 库，Python 依赖见 [requirements.txt](requirements.txt)。

[↑ 返回目录](#contents)

<a id="tested-versions"></a>

### 已实测环境与依赖版本

**已实测可行的 Ubuntu 64 位版本为 20.04.6 LTS（amd64）。** 下表的系统版本由维护者确认，Python 和依赖版本来自原可正常运行环境的 `pyvenv.cfg` 与包元数据；它们是复现环境参考，不表示所有同版本安装包或新初始化场景均已验证。

| 组件 | 原可运行环境记录的版本 |
| --- | --- |
| Ubuntu | **20.04.6 LTS，64 位 / amd64** |
| Linux 微信 | **4.1.13.26 / amd64**，仍须匹配 `wechat-layout.json` 中的二进制指纹 |
| Python | **3.8.10** |
| Frida | **16.7.19** |
| HTTPX | **0.28.1** |
| Pillow | **10.4.0** |
| httpcore | 1.0.9 |
| h11 | 0.16.0 |
| AnyIO | 4.5.2 |
| sniffio | 1.3.1 |
| typing_extensions | 4.13.2 |

**如果你的系统、微信或依赖版本与上述记录不同，请自行调整安装版本、`requirements.txt` 及必要的适配代码，并自行验证；本项目不保证适配，也不提供跨版本兼容承诺。** 不要仅修改微信版本号、文件指纹或校准验证标记来绕过检查。

安装器当前按 Ubuntu 软件源和 `requirements.txt` 的版本范围安装，**没有锁定整套实测环境**。例如 Frida 使用 `>=16,<17`，Pillow 使用 `>=9`，实际安装版本可能与上表不同；需要复现时，请自行固定所需依赖版本。

系统控件还包括 PyGObject（`python3-gi`）、AT-SPI、GTK/GDK、D-Bus、xinput、X11、XInput 和 XTest。代码要求 AT-SPI 2.0 与 GTK/GDK 3.0 接口，但原目录没有保存这些系统包的精确版本号，不能将接口版本当作安装包版本。可在实际可运行的 Ubuntu 上查询并记录：

```bash
dpkg-query -W -f='${binary:Package}\t${Version}\n' \
  python3 python3-venv python3-pip python3-gi \
  gir1.2-atspi-2.0 gir1.2-gtk-3.0 at-spi2-core dbus-x11 \
  xinput libx11-6 libxi6 libxtst6

.venv/bin/python --version
.venv/bin/python -m pip freeze
```

[↑ 返回目录](#contents)

<a id="quick-start"></a>

## 快速开始

[↑ 返回目录](#contents)

<a id="install"></a>

### 1. 获取项目并安装

将项目解压或克隆到 Ubuntu，在终端进入项目目录。以下以桌面上的目录为例，路径不同请自行替换：

```bash
cd ~/Desktop/autochat_wechat
bash reply_all.sh install
```

请使用普通桌面用户执行，**不要在整条命令前加 `sudo`**。安装过程会按需请求系统密码；终端输入密码时不显示字符是正常现象。

安装器会创建本机 `.venv`、安装依赖并补建缺失的本地配置，不覆盖已有配置。无需从其他机器复制虚拟环境，也不需要手动创建 Python 符号链接。

[↑ 返回目录](#contents)

<a id="launch-wechat"></a>

### 2. 启动并登录微信

```bash
bash wechat.sh
```

如果客户端安装在其他位置，也可传入实际的可执行文件路径：

```bash
bash wechat.sh /实际安装目录/wechat
```

完成登录后，**另开一个终端**进入同一项目目录，继续后续步骤。请关闭重复启动的微信实例。

[↑ 返回目录](#contents)

<a id="configure-api"></a>

### 3. 配置 API 和群聊策略

```bash
bash switch_api.sh
```

在浏览器面板分别配置“文字回复”和“图片识别”：

1. 填写方案名称、接口地址、模型名称和自己的 API key。
2. 设置请求超时及输出额度，按服务商要求选择是否添加 OpenCode session 头。
3. 主动点击“测试连接”，确认响应有效。
4. 点击“保存并启用”；其他方案可选择“存为备用”。

接口地址通常填写服务商提供的 API 基础地址，例如 `https://服务商域名/v1`；程序会在需要时追加 `/chat/completions`。请使用服务商实际支持的地址和模型，不要保留默认占位配置。

在“群聊回复策略”区域选择回复范围。使用“仅回复 @ 我的消息”时，填写群内实际显示的完整昵称，不加 `@`；多个昵称按行分隔。点击**标题右侧的“保存回复策略”**，等待出现“已保存并重新读取确认”。

**API 切换从下一次请求生效；群聊策略修改需要停止并重新启动自动回复。** 仅修改表单而未点击保存，不会改变配置。

[↑ 返回目录](#contents)

<a id="initialize-account"></a>

### 4. 初始化当前微信账号

准备一个双方同意的测试好友会话：打开其**独立聊天窗口**，保持窗口可见，清空输入框中的人工草稿，然后执行：

```bash
bash reply_all.sh init
```

也可直接指定测试好友窗口中的完整显示名称：

```bash
bash reply_all.sh init --contact "测试好友"
```

按终端提示完成验证：

1. 确认允许本次收发校验。
2. 请测试好友发送终端显示的第一条随机文字。
3. 使用你手机上的**同一个微信账号**，向该好友发送第二条随机文字。
4. 桌面程序发送一次初始化校验文字，并检查原会话窗口的发送回读。

全部通过后，程序生成私有的 `account-calibration.json`。取消、超时、身份不一致、窗口重名或发送未确认时，不保存新校准，原校准保持不变。初始化不调用模型，但会在确认后发送上述校验消息。

默认每条校验消息等待 180 秒，可调整为 30～600 秒：

```bash
bash reply_all.sh init --contact "测试好友" --seconds 300
```

[↑ 返回目录](#contents)

<a id="check"></a>

### 5. 检查运行条件

打开需要参与回复的聊天独立窗口，再执行：

```bash
bash reply_all.sh check
```

检查包含图形会话、依赖、微信文件指纹、校准文件、API 配置和独立聊天窗口。缺失的默认配置会补建；检查本身不调用模型、不发送消息，也不挂接微信。

校准文件检查通过，只表示文件有效，**不代表已经确认当前登录账号与文件中的账号相同**。切换账号后必须使用对应账号的校准。API 连通性请通过面板中的“测试连接”单独验证。

[↑ 返回目录](#contents)

<a id="start"></a>

### 6. 启动自动回复

```bash
bash reply_all.sh
```

**执行后会实际调用模型并自动发送微信回复。** 首次运行请先只打开测试会话，确认文字、群聊策略与图片识别符合预期，再扩大使用范围。

在运行终端按 **Ctrl+C** 停止。仅检查监听与窗口绑定时，可使用预览模式：

```bash
bash reply_all.sh --preview
```

预览模式不调用模型、不发送回复，但仍会监听并重建本次会话上下文。

[↑ 返回目录](#contents)

<a id="daily-use"></a>

## 日常使用

完成首次配置后，同账号、同微信构建通常只需：

1. 运行 `bash wechat.sh` 并登录微信。
2. 打开需要参与回复的聊天独立窗口。
3. 在另一终端运行 `bash reply_all.sh`。

| 命令 | 用途 |
| --- | --- |
| `bash wechat.sh` | 启动微信 |
| `bash switch_api.sh` | 打开 API 与群聊策略面板 |
| `bash reply_all.sh install` | 安装或补齐环境 |
| `bash reply_all.sh init` | 初始化当前账号 |
| `bash reply_all.sh check` | 检查运行条件 |
| `bash reply_all.sh --preview` | 预览监听和绑定，不调用模型或发送 |
| `bash reply_all.sh` | 启动自动回复 |
| `bash reply_all.sh --help` | 查看命令帮助 |

使用 `bash 文件名.sh` 启动不需要另外执行 `chmod +x`。项目目录可改名，但内部文件须保持相对位置。

[↑ 返回目录](#contents)

<a id="accounts"></a>

## 账号与版本

| 变化 | 处理方式 |
| --- | --- |
| 重启机器人、微信或 Ubuntu，账号与构建不变 | 不需要重新初始化 |
| 修改目录名、重建 `.venv` 或更换 API | 不需要重新初始化账号 |
| 换一个微信账号 | 为新账号执行 `init`，或恢复该账号在同适配版本下已生成的校准文件 |
| 切回以前的账号 | 停止机器人后使用该账号对应的校准文件 |
| 微信升级、程序指纹改变 | 先确认版本适配，不要修改验证标记绕过检查 |

账号校准不能在不同使用者之间共用。当前初始化只保存必要账号身份、版本布局摘要与验证标记，不保存测试好友、测试消息 ID 或进程记录。

[↑ 返回目录](#contents)

<a id="configuration"></a>

## 配置与目录

```text
autochat_wechat/
├── wechat.sh                 # 启动微信
├── reply_all.sh              # 安装、初始化、检查、运行
├── switch_api.sh             # 配置面板
├── defaults.json             # 可公开的默认配置模板
├── wechat-layout.json        # 支持的微信构建与接收布局
├── requirements.txt          # Python 依赖
├── README.md
├── DISTRIBUTION.md
└── *.py / *.js / *.html      # 接收、截图、发送及配置面板模块
```

下列文件由本机首次配置或运行生成，不属于公开模板：

| 文件或目录 | 用途 |
| --- | --- |
| `.venv/` | 当前 Ubuntu 的 Python 环境 |
| `api-profiles.json` | 私有文字／图片 API 方案与密钥 |
| `account-calibration.json` | 当前账号的私有校准 |
| `config.json`、`reply-tuning.json` | 基础配置与运行参数 |
| `group-mentions.json` | 群聊回复策略与昵称 |
| `vision-image-test.json` | 截图识别接口的备用配置；实际已保存的图片 API 方案优先 |
| `system-prompt.txt` | 追加的本地回复提示词 |
| `temp/` | 会话上下文、截图及模型请求记录 |

首次使用根据 `defaults.json` **只补建缺失文件，不覆盖已有设置**。旧版使用者的本地 `calibrated-receiver.json` 可兼容读取，但不应放入分发包。

[↑ 返回目录](#contents)

<a id="prompts"></a>

## 提示词在哪里修改

[↑ 返回目录](#contents)

<a id="text-prompts"></a>

### 修改文字回复的人设与语气

**日常调整优先修改项目目录里的 `system-prompt.txt`**，例如回复语言、语气、长度、人设和需要遵守的会话习惯。它是 UTF-8 纯文本文件，不叫 `systemprompt.txt`。首次安装会自动生成；发布源码中没有这个本地文件时，先运行安装命令。

例如可以写成：

```text
请使用中文自然交流，优先用简短、清楚的句子回答。
根据当前聊天上下文作答，不要编造不知道的事实。
需要解释时适当展开，不要机械地重复固定开场白。
```

还可以修改 `config.json` 中的 **`system_prompt` 字段**，它是基础系统提示词。编辑时保留其他 JSON 配置字段和合法格式，不要把一段提示词直接覆盖成整份 JSON 文件。

文字模型实际收到的系统提示词按以下顺序拼接，追加文件不是替换整份提示词：

```text
config.json 的 system_prompt
    + system-prompt.txt 的内容
    + reply_format.py 的 OUTPUT_RULE
```

`OUTPUT_RULE` 是程序附加的回复格式约束，例如只输出本轮回复正文、不加时间戳和发言人标签。通常不需要修改；若必须调整，在 [reply_format.py](reply_format.py) 中修改 `OUTPUT_RULE`，并确认输出仍适合直接发送到微信。各段提示词应保持一致，避免相互冲突。

[↑ 返回目录](#contents)

<a id="vision-prompts"></a>

### 修改图片识别提示词

当前截图流程的有效提示词位于 **[vision_analysis.py](vision_analysis.py) 的 `prompt_for(record)` 函数**，它会结合触发消息、时间和上下文生成识图指令。

**修改 `vision-image-test.json` 中的 `vision.prompt`，不会改变当前主程序截图流程的识图指令**：`analyse()` 会将其覆盖为 `prompt_for(record)` 的结果。[vision_client.py](vision_client.py) 中的“简单描述这个图片是什么”也只是通用备用文本，不是当前截图流程实际采用的完整指令。

调整图片分析侧重点时，不要删除结构化 JSON 输出要求。程序会严格检查 `target_found`、`message_type`、`content`、`visible_text`、`before_anchor`、`after_anchor` 和 `uncertain_reason` 七个字段，擅自改变字段或输出普通散文会使识别结果无法解析。

[↑ 返回目录](#contents)

<a id="default-prompts"></a>

### 修改默认模板与程序任务提示

| 想修改的内容 | 修改位置 | 何时生效 |
| --- | --- | --- |
| 当前使用者的回复风格、语言、人设 | `system-prompt.txt` | 重启自动回复 |
| 当前使用者的基础系统提示词 | `config.json` → `system_prompt` | 重启自动回复 |
| 程序附加的文字输出格式 | [reply_format.py](reply_format.py) → `OUTPUT_RULE` | 重启自动回复，修改后需验证发送正文 |
| 当前截图识别的指令 | [vision_analysis.py](vision_analysis.py) → `prompt_for(record)` | 重启自动回复，保留 JSON 输出协议 |
| 供首次使用者生成的默认提示词 | [defaults.json](defaults.json) 中的 `config.json.system_prompt` 和 `system-prompt.txt` | 只影响缺失配置文件的首次创建 |
| 引用、纯 @、相邻图片等场景的任务说明 | [session_context.py](session_context.py) → `reply_plan()`、`assemble()` | 修改代码后重启；属于高级任务规则，不是常规人设设置 |

修改 `defaults.json` **不会覆盖已经存在的 `config.json` 或 `system-prompt.txt`**。只想改变自己的当前回复风格时，直接编辑上述两个本地文件即可，不需要删除配置、重装环境或重新初始化账号。

所有提示词相关修改都应在停止自动回复后进行，然后运行 `bash reply_all.sh` 重新加载。API 面板中的模型、地址和密钥切换不等于提示词修改，也不需要因此重新校准账号。请不要把私人身份、联系方式或密钥写入将要公开的默认提示词。

[↑ 返回目录](#contents)

<a id="limitations"></a>

## 截图与上下文的边界

- 程序依赖聊天独立窗口的控件和当前画面，不是后台读取所有微信会话。
- 已通过新文字消息绑定的窗口可以继续参与截图；程序刚启动时，未绑定的会话先收到图片会出现截图失败。后续文字绑定后可以尝试补拍，但原图移出画面后不能保证识别。
- 请保持需要截图的会话窗口在屏幕范围内，避免关闭窗口或让目标内容移出画面。
- **每次启动自动回复或预览都会清空上轮会话上下文**，不会把上次运行的记录继续作为本轮历史。
- 模型输出不是确定结果，图片目标可能无法定位或识别。请先在测试会话中验证实际效果。

[↑ 返回目录](#contents)

<a id="faq"></a>

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 解压后没有 `.venv` | 正常。进入目录运行 `bash reply_all.sh install`；以点开头的文件也可能被文件管理器隐藏 |
| `No such file or directory` | 确认终端位于项目目录、文件名正确，命令末尾没有中文句号 `。` |
| 提示需要 Xorg | 注销后，在登录界面选择 **Ubuntu on Xorg** |
| 未找到唯一微信进程 | 先登录微信，并关闭重复实例 |
| 微信文件指纹不匹配 | 当前客户端构建不受支持，不能靠重复安装或改校准 JSON 解决 |
| 缺少账号校准 | 运行 `bash reply_all.sh init` |
| 初始化找不到测试窗口 | 打开唯一的好友独立聊天窗口，名称与提示一致，并确保两条校验文字可见 |
| 群昵称再次打开为空 | 等待面板显示已读取，点击标题右侧“保存回复策略”；如显示读取或保存失败，按具体错误处理 |
| 保存群策略后运行行为未变 | 停止并重新启动自动回复；API 切换与群策略的生效方式不同 |
| 找不到聊天独立窗口 | 确认窗口包含消息列表、输入框及发送按钮，且微信辅助功能可用；可尝试关闭微信后使用 `wechat.sh` 启动 |
| 有人工草稿时未发送 | 程序会拒绝覆盖草稿，先处理输入框中的人工内容 |
| 图片识别失败 | 检查窗口绑定、图片是否可见、视觉模型是否支持图片输入，以及 API 测试结果 |

[↑ 返回目录](#contents)

<a id="privacy"></a>

## 隐私与分享

会话文字和截图会发送到使用者配置的模型服务商，可能产生接口费用；请确认服务商的数据处理政策及自己的使用权限。

不要公开 `.env`、API key、账号校准、群昵称、私人提示词、`temp/`、日志或运行后的整套配置。`.gitignore` 已列出主要本地文件，但分享压缩包时仍需检查实际内容；外层原始整包和历史备份也不应一起上传。

项目不附带微信客户端、模型服务账号或现成 API key，不是微信官方产品。

[↑ 返回目录](#contents)

<a id="contributing"></a>

## 问题反馈与贡献

反馈问题时，请提供 Ubuntu 版本、桌面会话类型、微信版本、出错命令和经过脱敏的完整报错。不要直接附上 API 配置、账号校准、聊天记录或截图中的个人信息。

涉及微信新版本适配的修改，需要验证二进制指纹、消息布局、收发方向、窗口绑定和发送回读，不能仅修改版本号或关闭检查。提交修改时请说明验证环境与结果。

[↑ 返回目录](#contents)

<a id="license"></a>

## 许可证

**项目许可证尚未确定，当前没有 `LICENSE` 文件。** 本 README 不代表已经授予开源许可；正式公开发布前仍需确认源码权利、选定许可证并核对第三方依赖与适用条款。参见 [DISTRIBUTION.md](DISTRIBUTION.md)。

[↑ 返回目录](#contents)
