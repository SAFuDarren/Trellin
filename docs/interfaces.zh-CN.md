# 接口契约

[English](interfaces.md) · 简体中文

这里定义的是贡献者、插件、agent 和界面共同依赖的约定。在功能写出来之前先把这些约定定好，以后加新功能才不会到处返工。这些契约是在落实[架构决定](architecture.zh-CN.md)；版本和修改规则见[工程文档](engineering.zh-CN.md#版本与兼容性)。

## 各项契约的状态

| 契约 | 状态 | 何时冻结 |
| --- | --- | --- |
| [通用约定](#1-通用约定) | **稳定** | 现在 |
| [CLI 约定](#2-cli-约定) | **稳定** | 现在 |
| [数据库边界](#3-数据库边界) | **稳定** | 现在 |
| [HTTP API v1](#4-http-api-v1) | v1 草案 | 路线图 P1.2 完成时 |
| [事件 v1](#5-事件-v1) | **稳定** | P1.3 |
| [插件协议 v1](#6-插件协议-v1) | v1 草案 | P2.3 完成时 |
| [MCP 工具](#7-mcp-工具) | 草案 | P2.4 完成时 |
| [Worker 协议 v1](#8-worker-协议-v1) | v1 草案 | P3.3 完成时 |
| [Python 扩展接口](#9-python-扩展接口) | 草案 | P2.2 完成时 |

- **稳定**：修改按版本规则来。可以新增；不兼容的改动必须出新版本。
- **v1 草案**：整体结构已经定了，新代码按它写。细节在对应任务完成前还可以调整，任务完成后转为稳定。

机器可读的 schema 放在 [`research_multiplier/schemas/`](../research_multiplier/schemas/)。有 schema 的，以 schema 为准，本文负责解释。

| Schema | 对应契约 |
| --- | --- |
| `error.schema.json` | API 错误格式 |
| `event.schema.json` | 事件信封 |
| `plugin-manifest.schema.json` | 插件清单 |
| `plugin-request.schema.json` | 插件请求 |
| `plugin-response.schema.json` | 插件响应 |

---

## 1. 通用约定

以下约定适用于所有接口：API、事件、插件、MCP、CLI 的 JSON 输出，以及导出文件。

### 标识符

- 记录用 UUID 标识，写成小写的标准字符串。使用方把它当作不透明的值，不能从中解读含义。
- 组合引用用对象表示，不用需要解析的字符串。例如依赖图节点直接提供 `kind` 和 `ref` 两个字段。
- 工作区、用户、组织、插件、验证器各有自己的标识空间。插件名是小写、以点分隔的名字，例如 `org.example.r-verifier`。

### 哈希与规范化 JSON

- 哈希一律用小写十六进制的 SHA-256，字段名以 `_sha256` 结尾。
- 对 JSON 求哈希时，必须注明用的是哪种规范化形式：
  - `rm-json-1`：现有形式，目前所有记录都用它。键排序、两个空格缩进、UTF-8、不允许 NaN 和无穷大（即 `protocol.serialization.json_text`）。已有记录永久沿用（B7）。
  - `jcs`：RFC 8785 的 JSON 规范化方案。凡是跨语言的新契约（插件、worker、事件）都用它，因为其他语言也能精确复现。
- 文件类产出对原始字节求哈希。

### 时间、数量与金额

- 时间用 RFC 3339 字符串。API、事件和插件消息一律输出带 `Z` 后缀的 UTC 时间；数据库里存 `timestamptz`。
- 时长用整数毫秒，字段名以 `_ms` 结尾。
- 金额以整数微美元（`_microusd`）存储和计算。为了显示，可以附带一个以 `_usd` 结尾的十进制字符串；金额绝不用浮点数。
- token 数用整数（`input_tokens`、`output_tokens`）。

### 命名与取值

- JSON 字段名一律用 `snake_case`。
- 枚举值用小写的 `snake_case` 字符串。遇到不认识的值，使用方要能正常处理，显示为「未知」，不能崩溃。
- 布尔值用真正的布尔类型，不用 `"yes"` 或 `0` 代替。

### 兼容规则

- 使用方忽略不认识的字段。
- 提供方不改变已有字段的含义和类型。含义变了，就加新字段或出新版本。
- 响应里，已知为空的可选字段给出 `null`；只有当提供方的版本早于这个字段时，字段才会缺失。

### 文字与语言

- 文字一律用 UTF-8。研究内容按输入原样存储和返回，不做自动翻译。
- 给机器读的值（错误码、枚举、字段名）用英文标识。给人看的文字由界面根据稳定的错误码翻译（E6）。

---

## 2. CLI 约定

- 命令形式：`research-multiplier [--workspace PATH] <命令组> <动作> [选项]`。
- 输出：
  - 结果写到标准输出。
  - 诊断信息和错误写到标准错误。
  - 支持机器读取的命令接受 `--json`，输出一份符合第 1 节的 JSON。
- 退出码：
  - `0`：成功。
  - `1`：命令正常执行了，但结论是否定的，例如重放没有复现、核验没通过。
  - `2`：出错或用法不对。
- 标准输入不是终端时，命令不弹出交互提问。有破坏性或会对外发出的操作，必须显式加参数或确认。
- 配置变量：
  - 新增的运行环境和基础设施变量用 `RM_` 前缀，例如 `RM_HUMAN_DSN`。
  - 已有的模型适配变量保留 `RESEARCH_MULTIPLIER_` 前缀。
  - 密钥从环境变量或私有凭据文件读取，绝不从命令行参数读取。

---

## 3. 数据库边界

- **数据库表结构不是公开接口。** 内核 schema 里的表、视图、函数，任何版本都可能通过迁移改动。贡献者、插件、agent 和界面要通过 HTTP API、CLI、MCP 或 Python 内核接口访问数据，不能直接读写内核表。
- 插件可以有自己的 schema，存放私有数据（B6）。访问内核记录时，同样只能通过上面这些接口。
- 保护研究完整性的不变量留在数据库里：冻结的内容、证据与依据的关联、重验传播，以及 P3.1 之后的工作区隔离。接口层可以加更多检查，但不能依赖绕过这些不变量。
- 以后可能发布一些供外部工具读取的报表视图。届时会在这里列出、带版本，并像 API 一样保持稳定。

---

## 4. HTTP API v1

v1 草案，P1.2 完成时转为稳定。P1.2 生成的 OpenAPI 文档必须遵守以下规则。

### 路径、范围与格式

- 基础路径：`/api/v1`。请求和响应都是 JSON（`application/json; charset=utf-8`），事件流除外（见第 5 节）。
- 研究资源按工作区放在路径里：`/api/v1/workspaces/{workspace_id}/…`。
  - 本机模式下只有一个工作区，但它也有真实的 ID，因此客户端在本机和服务器两种模式下用同一套路径（D1、D5）。
  - 账号相关的资源不放在工作区下：`/api/v1/me`、`/api/v1/organizations/…`。
- 资源名用 `kebab-case` 风格的复数名词：`claims`、`library-items`、`inbox-items`、`progress-policies`、`runtimes`、`jobs`、`threads`、`events`。

### 认证

- 本机模式：请求带 `X-RM-Session` 头，内容是启动时生成的令牌（现有做法）。服务只监听本地回环地址。
- 服务器模式：
  - 浏览器用 `HttpOnly`、`Secure`、`SameSite=Strict` 的会话 cookie。
  - CLI 和 worker 带 `Authorization: Bearer <令牌>`，令牌是个人访问令牌。
- 所有会改变状态的请求，都要校验 `Origin` 是否匹配（浏览器），或者校验 bearer 令牌（CLI 和 worker）。
- 操作者身份一律从会话或令牌里取，请求体里绝不写「谁在操作」（B4）。

兼容接口 `POST /api/review` 接收 `evidence_id`、`action` 和 `reason`，继续返回原有的 `{ "id": … }`。数据库从 `0073` 起通过绑定的工作区解析本机研究者身份。旧客户端仍可发送 `actor`，此字段只在 `0073` 之前的数据库中用作显示名。版本化审阅接口的请求体无需提供操作者姓名或用户 ID。

### 方法与动作

- `GET` 只读，绝不改变状态。
- 对集合 `POST` 表示创建资源。
- 领域动作是对一个名词子资源 `POST`，例如：
  - `POST …/evidence/{id}/reviews`，请求体为 `{"decision": "approve" | "return", "reason": "…"}`；
  - `POST …/basis-items/{id}/withdrawals`。
- `PATCH` 只用于修改可变的设置。冻结的研究记录不能 `PATCH`，只能产生新版本或新的决定。
- `DELETE` 只用于真正可以丢弃的东西，比如草稿或令牌。研究记录只能撤回或被取代，永远不删除。

### 创建与并发

- 所有创建类的 `POST` 都接受 `Idempotency-Key` 头（一个 UUID）：
  - 同一个 key 配同样的请求体，返回第一次的结果；
  - 同一个 key 配不同的请求体，返回 `409 idempotency.conflict`。
- 这个头取代请求体里的 `request_id` 这类字段；旧接口上的 `request_id` 继续有效。
- 可变资源提供 `version`（或 `ETag`）。更新时带上 `If-Match`，不匹配返回 `412`。

### 列表

- 所有列表都返回一个对象：`{"items": [...], "next_cursor": "…" | null}`，绝不直接返回数组。
- 用游标分页：`?limit=`（默认 50，最多 200）和 `?cursor=`。
- 过滤条件用以字段命名的查询参数。排序用 `?sort=field,-other`，每个接口单独写明允许排序的字段。

[工作台 OpenAPI 文档](../research_multiplier/schemas/workbench.openapi.json)的首个试点是 `GET /api/v1/workspaces/{workspace_id}/claims`。它与 `/api/claims` 使用同一份数据，最多返回最近的 100 条可见已冻结命题。试点期间不接收查询参数，`next_cursor` 始终为 `null`。响应包含启动器选中的 `claim_id`（未选中时为 `null`），支持稳定引用编号的工作区还会返回 `claim_codes: true`。调整 OpenAPI 类型后，在 `web/` 中运行 `pnpm gen:api`。前端类型检查会验证生成的类型与契约一致。

同一份 OpenAPI 文档也涵盖只读接口 `GET /api/v1/workspaces/{workspace_id}/agent-work`。它在 `items` 中返回本地研究者的派工任务摘要，内容和排序与 `GET /api/agent-work` 一致。列表快照不分页，`next_cursor` 为 `null`。旧数据库未安装派工功能时，返回 `available: false` 和空列表。v1 接口要求本地会话和匹配的工作区，不接收查询参数，也不唤醒派工队列。

`GET /api/v1/workspaces/{workspace_id}/agent-work/{task_id}` 在 `{task}` 中返回研究者有权读取的派工详情，响应与 `GET /api/agent-work?task={task_id}` 一致，包含任务指令、验收标准和已记录的交付与审核。工作区和任务 UUID 通过路径传入。任务缺失或不可访问时返回 404。数据库尚未安装派工功能或已记录的交付不可用时返回 409，并使用不同的错误码。读取要求本地会话，且不接收查询参数。

### 耗时操作

预计超过一秒左右的操作返回 `202 Accepted` 和一个任务资源：`{"job": {"id": "…", "state": "queued"}}`。客户端通过事件或 `GET …/jobs/{id}` 跟进度。

### 错误

错误响应一律符合 `error.schema.json`：

```json
{"error": {"code": "review.not_pending", "message": "This evidence is no longer waiting for review.", "request_id": "…", "details": {}}}
```

错误码是小写、点分隔的标识（`模块.原因`），一经发布就保持稳定。界面按错误码翻译；`message` 是英文的兜底说明。状态码含义：

| 状态码 | 含义 |
| --- | --- |
| 400 | 请求无效（`request.invalid`、`request.too_large`） |
| 401 | 没有登录，或令牌无效 |
| 403 | 已登录，但没有权限 |
| 404 | 找不到。其他工作区的记录也返回 404，避免泄露它是否存在 |
| 409 | 状态冲突，例如记录已经变了，或者已经不在待处理状态 |
| 412 | `If-Match` 前置条件不满足 |
| 429 | 请求过于频繁 |
| 500 | 意外错误，不透露内部细节 |
| 503 | 数据库或依赖服务暂时不可用，可以重试 |

每个响应都带 `X-Request-Id` 头，同一个 ID 也出现在错误信息和服务端日志里。

### 安全响应头

现有策略继续作为契约的一部分：
- 严格的 CSP，只允许同源；
- API 响应带 `Cache-Control: no-store`；
- `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`，并禁止被嵌入框架。

---

## 5. 事件 v1

事件信封定义在 `event.schema.json`。

```json
{
  "id": "1024",
  "type": "evidence.reviewed",
  "schema_version": 1,
  "workspace_id": "…",
  "occurred_at": "2026-11-18T08:00:00Z",
  "actor": {"kind": "user", "id": "…"},
  "subject": {"kind": "evidence", "id": "…"},
  "data": {}
}
```

- `type` 的格式是「名词.动词过去式」，例如 `claim.frozen`、`evidence.recorded`、`evidence.reviewed`、`basis_item.withdrawn`、`job.started`、`job.finished`。
- `actor.kind` 取 `user`、`agent`、`worker` 或 `system`。
- 每种事件类型用 `schema_version` 给自己的 `data` 定版本。
- `id` 是字符串，在同一个工作区内递增；只在同一个工作区内比较大小。
- 投递语义是「至少一次」，使用方按 `id` 去重。只保证同一个工作区内的顺序。
- 服务器推送事件：`GET /api/v1/workspaces/{id}/events`。每条消息的 `id:` 是事件 ID，`event:` 是事件类型，`data:` 是事件信封。断线后用 `Last-Event-ID` 续传。
- `data` 只放 ID 和简短摘要，绝不放凭据。体积大或敏感的内容，由读者凭自己的权限通过 API 获取。

数据库版本 `0076` 为工作区内已提交的写入增加 `workspace.changed`。事件对象是工作区，数据包含受影响的区域，以及可以确定时的命题 ID。同一事务内涉及相同区域和命题的写入可共用一条通知。客户端通过已有接口读取最新状态。原有事件类型和游标的含义继续有效。

`GET /api/inbox` 通过 `workspace_events.coverage = "workspace-writes-v1"` 声明完整覆盖。工作台发送 `X-RM-Workbench-Updates: workspace-writes-v1` 后也可收到 SSE 注释 `: workbench-runtime-changed`，用于提示任务租约到期等运行状态变化。注释没有游标。

---

## 6. 插件协议 v1

v1 草案，P2.3 完成时转为稳定。对应的 schema：`plugin-manifest.schema.json`、`plugin-request.schema.json`、`plugin-response.schema.json`。

### 清单

每个插件在根目录放一份 `rm-plugin.json`，写明：

- `protocol_version`、`name`、`version`（语义化版本）、`kind`（`verifier`、`domain`、`tool` 或 `source`）、`description`、`license`；
- `entry`：启动命令，写成参数列表；
- `environment`：
  - `uv`，附锁文件；
  - `container`，附镜像摘要；
  - `system`，即声明要用的系统程序，例如 `lean`；
- `capabilities`：能处理的证据类型、领域，以及支持的操作；
- `permissions`：是否联网、是否调用外部模型、文件系统范围（默认只限工作目录 `workdir`）；
- `limits`：时间和内存上限。

### 调用方式

运行器的步骤：
1. 创建一个空的工作目录；
2. 把输入文件和 `request.json` 写进去；
3. 以这个目录为当前目录启动入口命令，并设置以下环境变量：
   - `RM_PLUGIN_REQUEST`：请求文件的路径；
   - `RM_PLUGIN_RESPONSE`：插件要写入响应的路径；
   - `RM_PLUGIN_WORKDIR`：工作目录的路径。

插件写好 `response.json` 后以 `0` 退出。标准输出和标准错误只当日志收集，不解析。以下情况都视为运行失败，不产生证据：退出码非零、超时、没有响应或响应无效、越权。

### 请求与响应

- 请求包含：
  - `protocol_version`、`request_id`、`operation`；
  - 对象：结论引用和陈述；
  - 输入：产出文件的路径和哈希，以及依据条目的 ID、版本、哈希和内容；
  - 参数和限制。
- 响应包含：
  - `protocol_version`、`request_id`、`status`（`completed`、`rejected` 或 `failed`）；
  - 验证器还要给出 `verdict`：`verified`、`refuted` 或 `inconclusive`；
  - 证据类型；
  - 输出的产出文件及其哈希；
  - `used_basis`：实际依赖的依据条目；
  - 带错误码的消息；
  - 插件自己的身份：名称、版本、`source_sha256`。

### 信任规则

- 内核按 schema 校验响应，并重新计算每个产出文件的哈希。
- `used_basis` 必须是请求里提供的依据的子集。
- 插件不能自己设定可信等级。可信等级由内核根据 verdict 和插件的注册信息算出（B2）。
- 响应里凡是对 JSON 求哈希，都用 `jcs` 规范化形式。

---

## 7. MCP 工具

草案，P2.4 完成时转为稳定。

- 服务命令是 `research-multiplier mcp`，先支持通过标准输入输出通信。
- 工具命名为 `rm_<模块>_<动词>`，例如 `rm_claims_get`、`rm_library_search`、`rm_inbox_list`、`rm_verify_run`。
- 读类工具没有副作用。写类工具只能产生提议（B2），并检查工作区的外部数据权限（D4）。
- 工具返回的结果遵守第 1 节的约定；出错时使用和 HTTP API 相同的错误码。

---

## 8. Worker 协议 v1

v1 草案，P3.3 完成时转为稳定。

- worker 用 bearer 令牌主动向外发起 HTTPS 连接，从不接受外来连接。
- 生命周期（接口都在 `/api/v1/workers/…` 下）：
  1. 注册，上报能力：Lean 版本、Mathlib 哈希、可用的模型 CLI、已装插件、资源上限；
  2. 每 15 秒发一次心跳；
  3. 申请租约，拿到一个任务和 `lease_until` 时间；
  4. 执行期间续租；
  5. 完成时提交结果并上传产出，或者主动释放任务。
- 租约过期后，任务回到队列。每个任务带有尝试次数编号；同一个任务 ID 加同一个尝试编号的提交是幂等的。
- 唤醒信号可以通过服务器推送事件下发；每 30 秒轮询一次作为后备。

---

## 9. Python 扩展接口

草案，P2.2 完成时转为稳定。

- 给贡献者和 Python 插件用的公开接口：
  - `research_multiplier.kernel`（P2.2 新增）：读写内核记录；
  - `research_multiplier.plugin_sdk`（P2.3 新增）：用 Python 写插件。
- 下列内容都属于内部实现，任何版本都可能变：
  - 以 `_` 开头的名字；
  - `_pinned/` 和 `_compat/` 下的模块；
  - CLI 处理函数所在的模块；
  - SQL。
- 内部模块要调用内核函数，不要各自重复写查询（A2）。内核模块建好之前，由 `research_multiplier.workflows` 里的流程函数承担这个角色。

---

## 修改契约

1. 开一个带 `needs-decision` 标签的 issue，说明要改什么、影响谁。
2. 对稳定契约只做新增的修改，可以在一个 PR 里合并，但 schema、本文档（中英文）和测试要同时更新。
3. 不兼容的修改必须出新版本（新的 API 路径、新的 `protocol_version` 或 `schema_version`），按工程文档的规定留出废弃过渡期，并写进更新日志。
4. 草案状态的契约可以在实现它的 PR 里调整，但必须同时更新本文档和 schema。
