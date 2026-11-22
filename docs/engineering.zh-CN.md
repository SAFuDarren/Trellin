# 工程与项目管理

[English](engineering.md) · 简体中文

这份文档说明项目怎么开发、怎么修改、怎么发布，以及作为开放社区项目怎么运作。三份文档分工如下：

| 文档 | 回答什么 |
| --- | --- |
| [架构决定](architecture.zh-CN.md) | 做什么、为什么这么做：带编号的决定和待定问题 |
| [路线图](roadmap.zh-CN.md) | 先做什么后做什么，以及怎样算做完 |
| [接口契约](interfaces.zh-CN.md) | 别人赖以开发的约定和契约：API、事件、插件、MCP、worker、CLI |
| 本文档 | 怎么做：语言、工具、流程、社区、发布 |

三份文档有冲突时，以架构决定为准，并在同一个 PR 里把另外两份改过来。

---

## 仓库结构

新增文件时按下表选择位置。根目录留给全项目的入口、贡献和许可文件，以及共用的工具、打包和发布配置。只服务于某个组件的文件放回该组件的目录。目录职责变化时，同步更新中英文两份表格。

| 路径 | 内容 | 语言 |
| --- | --- | --- |
| `research_multiplier/` | Python 包：CLI、各类流程、存储、策略、模型适配、随核心发布的插件、工作台服务端 | Python |
| `research_multiplier/storage/`、`research_multiplier/migrations/` | 持久化代码、Alembic 迁移版本及其执行的 SQL | Python、SQL |
| `research_multiplier/schemas/` | 记录和接口的机器可读 schema | JSON |
| `research_multiplier/workbench/app/` | 从 `web/` 构建并提交到软件包里的界面文件。前端改动时从源码重新生成 | 生成文件 |
| `research_multiplier/*/_pinned/`、`research_multiplier/_compat/` | 已记录指纹对应的验证器和适配器源码（B7） | Python |
| `lean/` | 通用 Lean 支持文件，以及固定版本的 Lean 和 Mathlib 配置 | Lean |
| `web/` | `src/` 中的 React 源码、`e2e/` 中的浏览器测试、前端配置和锁文件 | TypeScript |
| `desktop/macos/` | App 源码和资源、构建及打包工具和原生测试 | Swift、Shell |
| `brand/` | 产品品牌原稿和使用指南 | SVG、PNG、Markdown |
| `tests/` | 离线、PostgreSQL 和 Lean 测试、跟随版本的 `compat_validation/` 套件及小型合成夹具 | Python |
| `scripts/`、`.github/` | 维护脚本、CI 工作流、issue 和 PR 模板 | Python、YAML |
| `docs/` | `docs/README.md` 索引用户和工程文档，`docs/images/` 存放审阅过的图片，`docs/audits/` 存放带日期的项目审计摘要 | Markdown、图片 |

实际科研项目、可运行的公开示例和文档演示、个人 skill、Agent memory 和原始审计材料各用软件仓库外的独立目录。测试输出、运行日志和截图、数据库转储、外部验证包，以及 wheel 或源码分发包也放在仓库之外。每条测试线都使用独立的临时工作区和数据库，CI 输出放到 `RUNNER_TEMP` 一类的临时目录。Git 忽略的本地依赖环境或缓存可以留在 worktree。送审前通过 PR 差异检查文件路径。

---

## 语言与工具

| 语言 | 用在哪 | 版本规则 | 格式与 lint | 类型检查 | 测试 |
| --- | --- | --- | --- | --- | --- |
| Python | 内核、流程、CLI、服务端、worker、插件 SDK | 支持 CI 矩阵里的 Python 版本（目前是 3.10 和 3.14）；提高最低版本要写进发布说明 | ruff | pyright：整个包用基础模式，新写的内核模块用严格模式 | pytest |
| SQL（PostgreSQL） | 表结构、不变量、安全屏障视图、函数 | CI 矩阵里的 PostgreSQL 主版本，带 pgvector | 在 PR 里评审；命名规则见下文 | 约束和函数 | `postgres` 测试线 |
| TypeScript | React 界面 | Node 用 LTS 版本，固定在 `.nvmrc` 和 `packageManager` 字段里；用 pnpm，锁文件提交进仓库 | ESLint 和 Prettier | 严格模式的 tsconfig；API 类型从 OpenAPI 生成 | Vitest 和 Playwright |
| Lean | 通用验证器支持 | Lean 4.19.0 和固定版本的 Mathlib；升级必须单独做（见下文） | 遵循 Lean 惯例 | Lean 自身 | `lean` 测试线 |
| 插件使用的语言 | 核心之外的验证器和工具 | 在各插件的清单里声明 | 插件自定 | 插件自定 | 插件一致性测试 |
| Shell、YAML | CI 和部署脚本 | — | 脚本用 shellcheck 检查 | — | 由 CI 实际运行来检验 |

各语言的规则：

- **Python**
  - 基础安装保持零运行时依赖。需要第三方包的功能放进额外依赖组，比如 `postgres`。
  - 开发环境先按 AGENTS 的安装步骤配置。P0.3 会引入提交到仓库的 `uv.lock`。
  - `_pinned/` 里已经产生过证据的代码，一律不能修改；需要改动时，新增一个固定版本（B7）。
  - 脚本优先用 Python 写，不用 shell，因为 CI 也在 Windows 上跑。
- **SQL 与迁移**
  - 迁移只能往前走：`downgrade()` 直接报错，提示用备份恢复。现在已经是这样做的。
  - 已经合并的迁移不能再修改。
  - 每个迁移只做一件事，并在 docstring 里写清楚做什么。
  - P2.1 之后，内核对象里不再新增领域相关的 `kind IN (...)` 列表，也不再出现 `'lean-core'` 字面量（B3、A3）。
  - P3.1 之后，每张属于工作区的新表都要带工作区 ID 列和行级安全策略，并且两者写在同一个迁移里（D5）。
  - 内核对象放在内核 schema，插件对象放在插件自己的 schema（B6）。
- **TypeScript 与 React**
  - 不写内联脚本和内联样式，也不加载外部资源，这样服务端的 CSP 可以保持严格。
  - 所有面向用户的文字都来自翻译文件：默认英文，同时提供中文（E6）。
  - 只通过自动生成的客户端调用 API，不手写响应类型。
  - 来自服务端的数据用 TanStack Query 获取和缓存；界面自身的状态留在组件里。
  - 无障碍目标：WCAG 2.1 AA。
  - 使用 [`foundation.css`](../web/src/styles/foundation.css) 中的交互样式。每个独立可见的操作区最多有一个实底主按钮 `.button.primary`。次要操作用描边按钮 `.button`，辅助操作用低强调的文字或图标。操作区内打开确认步骤后，主按钮随之切换到确认操作。
  - 使用工作台字号变量，页面标题、区块标题和卡片标题分别为 22、16 和 14 px。海军蓝仅用于选中、焦点、主操作和运行状态。按主题搭配强调色和前景色，同时为带浅色底的选中项提供实色标记。
- **Lean**
  - 升级 Lean 或 Mathlib 必须单独开一个 PR：
    - 记录新版本号，并运行 `lean` 测试线和发布回归。
    - 按现有的升级流程操作（`lean-reset-after-upgrade`、回归重放）。
    - 旧版本产生的证据原样保留，不重写。
- **插件**
  - 可以用任何语言写，但必须带清单，并通过一致性测试（C1–C3）。

### 新代码应该写在哪

| 如果你要加的是…… | 写在 | 原因 |
| --- | --- | --- |
| 一条研究规则、一种记录类型或一个查询 | Python 流程函数加 SQL | CLI 和 API 共用一份实现（A2）；不变量由数据库保证（B6） |
| 一个页面或一种交互 | `web/` 里的 TypeScript/React | E1 |
| 一个验证器或领域检查器 | 插件，用最适合该领域的语言 | C1 |
| 一个给外部 agent 用的工具 | 一个 MCP 工具，调用现有的流程函数 | C4 |
| 一个独立的高并发组件 | 先讨论；只有记录了决定，才用 Go 或其他语言 | E1 |

---

## 开发流程

1. **先开 issue。** 稍有规模的改动都从一个 GitHub issue 开始。issue 写明要解决的问题、验收标准和相关决定的编号。小修小补可以直接提 PR。
2. **建分支。** 有对应任务时，使用认领时记录的 `type/issue-short-topic` 分支名，例如 `feat/12-event-log`。从上游 `main` 创建，在任务分支提交。
3. **写提交说明。** 采用 Conventional Commits 格式：`类型(范围): 摘要`。
   - 类型有：`feat`、`fix`、`docs`、`refactor`、`test`、`build`、`ci`、`chore`。
   - 范围填所属的层或模块，例如 `kernel`、`lean`、`web`、`migrations`。
   - 每次提交只做一件事。
4. **提 PR。** 填写模板和提交前检查清单。仓库所有者评审其他贡献者的 PR，并核对自己 PR 的验收证据。
5. **合并。** 所有必需检查通过后，只由 GitHub 仓库所有者 squash 合并上游 PR，包括自己开的 PR。保留 Conventional Commits 格式的标题，供更新日志使用。
6. **涉及架构决定。** 如果改动需要新增一条架构决定，或者修改已定的决定，就在同一个 PR 里同时更新 `architecture.md` 和 `architecture.zh-CN.md`：
   - 新决定按所在章节顺延编号。
   - 被取代的旧决定不删除，标注「已被某某取代」。
   - 待定问题只能由维护者做决定。

### 提交前检查清单

每个 PR 都要逐项确认；不适用的项写明原因。

- [ ] 改动符合它涉及的已定决定，并注明编号。
- [ ] 没有建立在待定问题之上（见 `architecture.zh-CN.md` 的 G 节）。
- [ ] 业务逻辑写在流程函数里，不写在 CLI 或 HTTP 处理函数里（A2）。
- [ ] 表结构的修改是一个新的、只往前走的迁移，能从上一个发布版本升级上来，并且该有的不变量和安全策略一项不缺。
- [ ] 每次写操作都记录用户，并追加对应事件（B4、B5；P1 之后适用）。
- [ ] 发往工作区外部的数据，事先经过授权，并留有记录（D4）。
- [ ] 同步更新了 OpenAPI 文档和生成的 TypeScript 类型（D1；P1.2 之后适用）。
- [ ] 所有面向用户的文字都已加入中英文翻译文件（E6）。
- [ ] 改动有对应测试线的测试覆盖，CI 通过。
- [ ] 用户文档的中英文版本都已更新；如果改动影响首次使用，两份快速入门也已更新。
- [ ] 如果命令、决策流程、模型或网络的使用方式变了，同步更新了 `research_multiplier/agent_guide.md`。
- [ ] 用户或插件作者能感知到的变化，都写进了 `CHANGELOG.md`。
- [ ] 日志、URL、测试数据里没有出现密钥、数据库连接串或研究内容。

---

## Issue 与计划

所有任务、缺陷和里程碑都在 **GitHub Issues** 和 **GitHub Projects** 看板上跟踪。

- **里程碑**使用路线图的阶段名称。路线图里的每个任务对应一个 issue，大任务可以开一个总 issue 再拆子 issue。已有的路线图任务编号（如 `P1.3`）保留，供引用。
- **范围和类型标签**包括 `layer:kernel`、`layer:plugin`、`layer:collab`、`layer:ai`、`layer:entry`、`area:web`、`area:migrations`、`area:lean`、`area:docs`、`type:bug`、`type:feature`、`type:refactor`、`type:docs`、`type:chore` 和 `type:incident`。卡在待定问题上的任务使用 `needs-decision`。用 `good first issue` 和 `help wanted` 邀请贡献。
- **优先级。** 每个开放 issue 使用一个紧急程度标签，按以下顺序处理可推进的工作。`priority:p0` 是需要立即处理的严重阻塞，`priority:p1` 是本批次的紧急事项，`priority:p2` 在更紧急的事项之后处理，`priority:p3` 是紧急程度较低的积压事项。同一级先验收已有结果并记回原 issue，再认领最早可做的 issue。遇到阻塞时，在原 issue 记录原因和依赖，再处理下一条可推进的事项。调整优先级时在原 issue 写明原因。参见 [GitHub 队列](https://github.com/SAFuDarren/Trellin/issues/112#github-queues)。
- **看板列**依次为待分诊、可开工、进行中、评审中、已完成。范围、依赖或决定尚待澄清时用 `status:triage`。验收条件、依赖和[模型与环境要求](#难度与模型匹配)都明确时用 `status:ready`。认领评论和 `status:in-progress` 记录负责人。有可评审的 PR 时加 `status:in-review`，并保留有效的认领。见[并行开发](#并行开发)。
- **分诊：** 维护者每周过一遍新 issue：打标签，归入里程碑或关闭，并关联卡住它的决定。
- **维护路线图：** 新增、拆分或调整任务顺序时，`roadmap.md` 和 `roadmap.zh-CN.md` 要与 issue 一起更新。进度记在 issue 里，不记在路线图里；见[及时更新状态和记录](#及时更新状态和记录)。

---

## 版本与兼容性

1.0.0 是第一个公开发布版本。每个合并到 main 的 PR 默认使用下一个补丁版本号，例如 `1.0.22` → `1.0.23`。经维护者批准的功能里程碑递增次版本号，并将补丁版本号归零。经批准的主要里程碑开启下一代版本，或包的公开行为出现不兼容的改动时，递增主版本号，并将次版本号和补丁版本号归零。递增包版本号、创建标签和发布版本是独立操作。

包版本以 [pyproject.toml](../pyproject.toml) 中固定的 `[project].version` 为准。[兼容回归套件的版本标记](../tests/compat_validation/PACKAGE_VERSION) 同步为候选包版本，并保留[原始来源记录](../tests/compat_validation/PROVENANCE.md)和归档基线。必需的发布回归会核对该标记与实际安装的 wheel 版本。

每个公开接口也各自带版本。

| 接口 | 版本标记 | 规则 |
| --- | --- | --- |
| Python 包和 CLI | 包版本号（语义化版本） | 不兼容的改动升主版本号，并写进更新日志 |
| HTTP API | 路径前缀 `/api/v1` | 只做新增时留在 v1；有不兼容的改动就新增 v2，v1 至少再保留一个次版本 |
| 插件协议 | 请求和清单里的 `protocol_version` | 运行器同时支持当前和上一个协议版本 |
| 事件内容 | 每种事件类型各自的 `schema_version` | 使用方忽略不认识的字段；字段含义变了就换新版本 |
| 数据库结构 | Alembic 的最新版本号 | 只往前走；每次发布写明它从哪个版本升级而来 |
| 验证器 | 名称、版本、源码指纹 | 证据始终绑定产生它的那个确切的验证器（B7） |

新接口可以在文档里标注为「实验性」。实验性接口允许在次版本里改动，但必须写进更新日志。

要废弃的功能，至少提前一个次版本给出警告，然后再删除。

---

## 测试策略

| 测试线 | 需要什么 | 何时运行 | 覆盖什么 |
| --- | --- | --- | --- |
| `offline` | 只需要 Python | 每个 PR | CLI 参数解析、离线研究简报、重放、纯逻辑、服务端的输入校验 |
| `postgres` | 一个用完即弃、装有 pgvector 的 PostgreSQL | 每个 PR | 从空库执行迁移、从上一个发布版本升级、不变量、各类流程、API 接口、行级安全（P3.1 之后） |
| `lean` | 固定版本的 Lean 和 Mathlib | 定时运行，并在每次发布前运行 | Lean 核验、重放、升级流程 |
| `web` | Node | 改动涉及 `web/` 或 API 的 PR | Vitest 单元测试；用 Playwright 在测试服务器上测功能对齐和各分区的操作流程 |
| `plugins` | 每个插件自己的环境 | 改动涉及运行器或插件的 PR | 参考插件的一致性测试 |
| 安装包冒烟测试 | 构建好的安装包，在 Linux、macOS、Windows 上运行 | 每个 PR | 从安装包运行安装和首次使用的命令（现已有） |

规则：

- 测试一律不碰真实的研究工作区和数据库，只用临时工作区和用完即弃的数据库。
- 测试数据里不放真实的研究内容，也不放任何凭据。
- 修 bug 时要附一个测试：修复前失败，修复后通过。

---

## 工程审计

每个路线图阶段结束时，用维护者保存在软件目录外的标准做一次工程成熟度审计：根据仓库里的证据给 12 个维度 × 4 项检查打分，运行或记录六项压力测试，结果写进 [docs/audits](audits/README.zh-CN.md)。每条发现有固定编号，并转成路线图任务或 issue，下一次审计报告它的进展。决定把精力投在哪里，看审计之间的变化趋势，而不是某一次的绝对分数。

## 并行开发

可能有多个人和多个编码 agent 同时在这个仓库里工作。以下规则用来避免彼此冲突；`AGENTS.md` 里有每个 agent 都会读到的简版。

**分配工作**
- 使用自己的 GitHub 身份。贡献者在自己的 fork 推送分支，向上游 `main` 提交 PR。仓库所有者的凭据留在其本人账户中。上游写权限归仓库所有者，所有者和贡献者的 PR 适用相同的必需检查。见 [GitHub 当前规则](https://github.com/SAFuDarren/Trellin/rules)。
- 在未关闭的 issue 下申请认领，写明 GitHub 账号、fork 仓库、分支、预计改动路径、模型及推理档位或纯人工处理，以及验证所需工具。所有者在 issue 确认来源和匹配度后开工。每个人或 Agent 同时只做一个任务。

  所有者在上游的本地检出中记录已确认的 fork 和改动范围。

  ```sh
  python scripts/tasks.py claim 123 --agent contributor-web --branch fix/123-short-topic \
    --repository contributor/Trellin --contributor contributor --scope web/src
  ```
- 在上游检出运行 `python scripts/tasks.py board`，核对持有者、分支和关联 PR。认领超过 48 小时后检查进展。所有者记录释放并处理认领冲突。
- PR 用 `Closes #N` 关联已满足全部验收的 issue，部分实现用 `Refs #N`。Task link 核对 PR 作者、来源仓库和分支是否与所有者确认的 fork 认领一致。没有对应任务的小改动由所有者添加 `no-issue` 标签。
- 保留认领直到评审结束。交接时在 issue 留下已测试的提交 SHA、证据、剩余项和下一条验证命令。改动范围或模型变化时重新确认。相互重叠的工作按可独立评审的依赖拆分。

**隔离**
- 一个任务对应一个从上游 `main` 开出的分支，以及一个 git worktree。谁都不改别人的分支或 worktree。
- 本地资源各用各的：
  - 测试数据库名称不能重复；
  - 服务用 `--port 0` 启动，或者先确认端口空闲；
  - 案例副本、日志和构建输出放在源码树之外的临时位置，详见[仓库结构](#仓库结构)。

**容易冲突的地方**

| 位置 | 规则 | 由什么强制 |
| --- | --- | --- |
| Alembic 迁移 | 需要维护者批准。开 PR 时再确定编号；如果 `main` 已经有了新迁移，就改用下一个编号并更新 `down_revision` | `tests/offline/test_migrations.py`（版本号不重复、只有一个最新版本、编号与文件名一致） |
| 构建好的界面 `research_multiplier/workbench/app/` | 每次 rebase 后重新生成，不手动合并 | 前端 CI 任务（提交的构建必须与源码一致） |
| 生成的 API 类型（P1.2 之后） | 每次 rebase 后重新生成 | CI 差异检查（P1.2） |
| 翻译文件 | 冲突时双方的条目都保留 | 中英文条目一致性测试 |
| 架构决定、接口契约、schema | 只能在单独的 PR 里修改，并经维护者批准 | CODEOWNERS 请求评审，仓库所有者决定合并 |

**合并**
- 申请评审前和合并前，获取最新的上游 `main` 后 rebase。在已提交的候选版本上运行 `python scripts/check_package_version.py`，并重新跑必需检查。上游远端名为 `upstream` 时使用 `--base upstream/main`。经维护者批准的次版本或主版本递增分别使用 `--bump minor` 或 `--bump major`。如果 main 已推进，依据新的基准更新两个版本文件后再检查。
- 每个 PR 只做一个任务。
- 用 squash 合并，标题采用 Conventional Commits 格式。
- 在任务范围之外发现的问题，另开 issue，不要顺手改在当前分支里。

#### 所有者合并前检查

贡献者可以并行准备 PR，由所有者确定合并顺序并串行合入。PR 进入合并队列时再确认下一个补丁版本。基线推进后，由贡献者 rebase、更新两处版本标记并验证新提交。

所有者先在 GitHub 审核差异，再从当前上游 `main` 的干净检出运行 `python scripts/review_merge.py PR --head FULL_REVIEWED_SHA`。替换 PR 编号和已审核的完整 SHA。使用同一 SHA 加上 `--merge` 执行 squash 合并。命令读取 GitHub 的 PR 和 issue 元数据，核对最新基线及版号。即使旧的 Task Link 已通过，也重新核对所有者确认的认领，并要求所审提交上来自指定来源的必过检查全部通过。GitHub 的现有规则继续对合并请求生效。

首次贡献者的 CI 由所有者审核变更文件后批准。首次合入外部贡献前，在 [#24](https://github.com/SAFuDarren/Trellin/issues/24) 完成许可证复核。外部 PR 变更工作流定义或合并校验时，单独审阅后才使用 `--reviewed-workflow-changes`。合并检查从受保护的检出运行，只读取候选提交与元数据。候选代码的测试在隔离的 PR CI 中运行。

新人验收由一位使用独立 GitHub 账号的贡献者，从 [good first issue](https://github.com/SAFuDarren/Trellin/labels/good%20first%20issue) 和公开指南走完克隆到可评审 PR 的流程。在 [#267](https://github.com/SAFuDarren/Trellin/issues/267) 记录认领、签署提交、夹具验证、CI 审批、一次审核或交接，以及贡献者和所有者的权限验证结果。另把安装耗时、CI 重跑次数和人工评审时间记入 [#23](https://github.com/SAFuDarren/Trellin/issues/23) 的交付审计。

### 难度与模型匹配

标记 issue 可开工前，写清基准提交 SHA、范围、前置条件、复现步骤、验收标准和所需验证环境。难度与优先级分别记录，五项各 0–3 分。

| 维度 | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| 范围 | 局部文档或测试 | 单组件 | 同子系统的多个组件 | 跨层或发布边界 |
| 不确定性 | 修法已知 | 复现明确 | 有多种可行方案 | 需厘清问题或契约 |
| 出错影响 | 易回退 | 局部行为 | 持久状态或兼容性 | 权限、迁移或证明完整性 |
| 验收成本 | 一项确定性检查 | 一条测试通道 | 多条通道或服务 | 真机、精确回放或迁移演练 |
| 依赖 | 无 | 一项前置 | 需协调多项改动 | 分步上线或独立验收 |

从[模型登记表](../.github/agent-models.json)查看 [Artificial Analysis Coding Agent Index v2.1](https://artificialanalysis.ai/agents/coding-agents/comparisons/codex-vs-claude-code#models) 的初版门槛。评分按 Agent 运行器、模型、推理档位一起看。运行 `python scripts/model_routing.py D2` 查看当前达标配置，可把 D2 换成 issue 难度。记录匹配时使用的榜单版本和日期。

| 难度 | Issue 得分 | 参考分数线 | Codex 首选 |
| --- | ---: | ---: | --- |
| D1 | 0–3 | 30 | GPT-6.2 Sol low，37 |
| D2 | 4–7 | 45 | GPT-6.2 Sol medium，45 |
| D3 | 8–11 | 50 | GPT-6.2 Sol xhigh，50，或 GPT-6.2 Astra high，52 |
| D4 | 12–15 | 52 | GPT-6.2 Astra high，52，另需独立验证 |

跨层持久状态设计、Mac 真机验收和 Lean 回放至少 D3。合并权限、CI 信任规则、受保护的迁移和不可逆科研证据变更定为 D4。参考线用于派单。D3/D4 仍由所有者确认范围和证据，D4 另加独立验证。

满足门槛和环境要求后，优先选成本较低的配置。Web 任务需要 React/TypeScript、键盘交互和 Playwright。后端和证明分别使用[离线、PostgreSQL 和固定版本的 Lean 测试通道](../tests/README.md)，Mac 验收见[桌面指南](desktop.zh-CN.md)。仓库权限任务要会核对 GitHub Rulesets、fork 权限和签署提交。模型配置未收录时，凭同类任务证据向所有者申请例外。缺必需工具时，把验收交接给有对应环境的人，或只认领范围明确的复现或审核工作。验收证据仍须齐全。

[模型目录工作流](../.github/workflows/model-catalog.yml)每天检查 OpenAI、Anthropic 和 Google 的官方目录，为新型号开待测 issue。目录尚未收录的发布或快照可用[模型分数更新表单](https://github.com/SAFuDarren/Trellin/issues/new?template=model_score.yml)登记。标题用 `Model score update — Provider / exact-model-id`，让工作流识别同一条记录。拿到同版本可比分数后提 PR 更新模型表。PR 记录模型、推理档位、榜单版本、日期、分数和来源。所有者合并后生效。榜单换版或模型退役时复核既有推荐。

### 分支生命周期与每周盘点

在仓库检出中运行 `python scripts/tasks.py branches`。命令读取 `origin` 的实时分支头、本机分支、worktree、GitHub PR 和有效认领。加 `--json` 可保存带时间的快照。维护者每周查阅一次，通过 issue 将停滞分支和到期的保留记录交给负责人复核。

[Branch inventory 工作流](../.github/workflows/branch-inventory.yml)每周运行一次，也可手动运行。它在 [#205](https://github.com/SAFuDarren/Trellin/issues/205) 更新同一条远端分支报告，列出待复核的候选。工作树和提交覆盖范围在本机检出中检查。

| 状态 | 依据 | 负责人和动作 |
| --- | --- | --- |
| 活跃 | 分支有开放 PR、被开放 PR 用作基线，或已认领并有近期提交。新认领有 48 小时提交进展。近期提交但未认领、或无法核实提交日期的分支暂留复核。 | 任务负责人继续开发或交接。 |
| 待退役 | 当前分支头与已合并或明确关闭的 PR 最终分支头相同，且没有开放 PR 依赖或有效认领。 | 任务结束当天由负责人走[退役流程](#清理分支和-worktree)。未合并的工作由维护者决定。 |
| 停滞 | 没有活跃 PR 或有效认领，且七天没有提交。认领超过 48 小时、认领后也没有近期提交，同样列为停滞。 | 负责人记录待保留内容或提出退役。无人负责的分支由维护者处理。 |
| 保留 | 仓库默认分支或已登记在[保留记录](preserved-branches.json)中的分支。分离 HEAD 或尚无提交的检出先保留，交人工复核。 | 登记的负责人在复核日确认保留理由。维护者在每周盘点时跟进到期记录。 |

登记保留分支时填写 `branch`、`reason`、`owner` 和 `review_on`。复核日用 YYYY-MM-DD 格式，登记前确认负责人和日期。Squash 合并要对照 PR 最终分支头和合并提交。

报告标出未提交的 worktree、无法读取状态的检出、忽略文件、被锁定或可清理的 worktree，以及当前任一已配置远端的分支头都不可达的本机提交。远端分支头或本机历史不完整时显示未知。盘点只读取状态，不拉取、清理或删除引用。处理前按下面的退役步骤核验。

### 清理分支和 worktree

本机检出由原负责人清理，或在 issue 里明确交接。记录分支、已评审的提交和剩余工作。负责跟进评审时保留认领，交接或退出跟进时释放认领。GitHub 设置由维护者决定。

**移除本机 worktree。** 确认检出和任务的对应关系。用 `git -C "$worktree" status --short --untracked-files=all --ignored=matching` 核对文件，同时检查未完成的 Git 操作、使用者和进程。先保存有用的改动与产物，记录恢复位置并核实能取回。维护者授权交接时，可以清理有开放 PR 的干净且闲置的本机检出，远端分支和认领继续保留。从另一个检出目录运行 `git worktree remove "$worktree"`。

**清理分支。** 开放任务的分支继续保留。合入后用 `gh pr view "$pr" --json state,headRefOid,mergeCommit,baseRefName` 确认 `MERGED`，记录已评审的最终分支头。拉取目标分支，确认 GitHub 报告的合并提交在其历史中。Squash 合并后，原分支头可能不在目标分支的提交历史里。任务未合入但已明确关闭时，记录已保存的内容，并由维护者决定是否清理。

用 `gh pr list --state open --head "$branch"` 和 `gh pr list --state open --base "$branch"` 查找依赖。堆叠 PR 的负责人先改用已合入的基线，只重放自己的改动并通过必需检查，再清理父分支。对照本机分支头、远端分支头和评审提交，保存额外改动。任务关闭或释放且检查通过后，尝试 `git branch -d "$branch"`。Git 拒绝时交维护者处理。远端任务分支由负责人最后一次核查依赖后清理。

合并后，维护者在有完整历史的检出中先拉取上游 `main`，再运行 `python scripts/retire_merged_branches.py` 预览可退役分支。用 `--protect-checkout PATH` 指定其他需要保护工作树的本机克隆。预览确认合并提交可从当前 `main` 到达，且 GitHub 仍可从 PR 的 head 引用取回准确的原分支头。核对外部引用并与其他持有人协调后，使用相同的保护路径和 `--apply BRANCH` 退役指定分支。命令重新核验，并仅在远端头仍匹配时删除。在原任务记录 PR、原分支头和合并提交，便于恢复。GitHub 自动删除来源分支保持关闭，堆叠 PR 和正在检出的分支由维护者核对。

### 压力测试

一个阶段开始并行开发之前，演练多人同时认领同一 issue，以及两个互不重叠的任务独立合入。核对只有一人认领成功，各自使用独立的 worktree 和测试资源，合入后两项改动的必需检查都通过。把结果记入[工程审计](audits/README.zh-CN.md)。

只使用仓库内容的[交接演练 #9](https://github.com/SAFuDarren/Trellin/issues/9)应让下一位 agent 从 `AGENTS.md` 找到源码、生成界面、测试和输出的位置，并指出哪些情况会阻止清理任务分支。把结果记录到 issue。

## 及时更新状态和记录

每类记录只有一个存放位置。哪个 PR 改变了事实，就在同一个 PR 里更新对应记录。

| 记录 | 唯一依据 | 何时更新 |
| --- | --- | --- |
| 任务状态和结果 | 原 GitHub issue 及其里程碑 | 用认领工具记录负责人。在 issue 里[交接可验收的结果](#交接验收结果)，验收完成后[关闭 issue](#关闭-issue) |
| 计划和验收标准 | `docs/roadmap.md` 和 `.zh-CN.md` | 只在新增、拆分、调整任务顺序或修改验收标准时更新，不用它标记进度。同一个 PR 里同步更新 issue |
| 跟踪类 issue（例如公开条件，#18） | 该 issue 里的勾选清单 | 完成某项的 PR 合并后，勾掉对应条目 |
| 架构决定和待定问题 | `docs/architecture.md` 和 `.zh-CN.md` | 某个问题有了结论时：把它从 G 节移到「已决定」那一行，新增或修改对应编号的决定，并关闭它的 `needs-decision` issue |
| 接口契约 | `docs/interfaces.md` 和 schema | 按该文档「修改契约」一节的流程办 |
| 用户能感知的变化 | `CHANGELOG.md` | 将用户或插件作者能感知的变化记在顶部的候选包版本下 |
| 包版本号 | `pyproject.toml` | 按照[版本规则](#版本与兼容性)以最新的 main 确定候选版本，同时更新兼容套件的版本标记。发布时遵循[发布准备](release.md) |
| 用户文档 | `docs/`（中英文）、两份快速入门、`research_multiplier/agent_guide.md` | 研究者能看到的命令、界面、决策、模型使用或联网行为有变化时 |
| 审计发现 | `docs/audits/` 里的审计记录（写完后不再修改） | 由下一次审计报告每条发现的进展；实际进展记在该发现指向的 issue 或任务里 |

一个里程碑的所有 issue 都关闭、并且满足该阶段的出口标准后，关闭这个里程碑。

### 交接验收结果

有可评审或试用的结果时，更新原 issue。用 `Result and acceptance` 小节记录 PR 或试用版本的入口、实际验证的提交或构建、验证证据和剩余验收项。新结果替换旧结果时，更新这一个小节。验收仍有剩余项时保留开放状态。

有可评审的 PR 时加 `status:in-review`。负责跟进评审期间保留认领。交接跟进责任时，先在 issue 里记下分支、版本和剩余项，再用认领工具释放任务。

## 关闭 issue

issue 的每一条验收标准都满足、并且有测试或 CI 检查能证明时，这个 issue 就算完成，应该马上关闭。已经解决却还开着的 issue，会误导所有看任务板的人。

- **由 PR 关闭。** 只有当 PR 满足全部验收标准时，才在 PR 描述里写 `Closes #N`，合并后 issue 会自动关闭。如果只满足了一部分，写 `Refs #N`，并在 PR 里列出还剩什么。
- **手动关闭。** 如果验收标准是由几个 PR 共同完成的、是在做别的 issue 时顺带完成的，或者不需要写代码，就手动关闭，并留言写明是哪些 PR 或提交、每条标准怎么证明：`gh issue close N --comment "..."`。
- **每次合并之后**，检查这个 PR 涉及的 issue：关联的 issue 已关闭，`status:in-progress` 标签已去掉，跟踪类 issue（例如 #18）里的勾选项已勾上。再看看这次合并还顺带解决了哪些其他 issue，一并留言关闭。
- **没做完。** 有任何一条标准没满足，就不要关闭。在 issue 里写明还剩什么，或者把剩下的部分拆成新 issue 并互相链接。
- **不再做。** 决定不做的 issue，以 "not planned" 关闭，并写明原因。
- **重新打开。** 如果出现回归，说明其实没有完成，就重新打开 issue，并链接到回归问题。

## CI 运行

- **每个任务都有时间上限。** 每个 job 都设置 `timeout-minutes`，取平时耗时的两到三倍。新增 job 必须设置。GitHub 默认是六个小时，一次卡住就会白白耗掉一个下午。

  | 任务 | 平时耗时 | 上限 |
  | --- | --- | --- |
  | Lint and offline tests | 1 分钟 | 15 分钟 |
  | Web interface (React) | 2 分钟 | 20 分钟（端到端测试这一步 10 分钟） |
  | Installed package smoke check | 1 分钟 | 20 分钟 |
  | Required release regressions | 14 分钟（按路径跳过时约 2 分钟） | 30 分钟 |
  | DCO | 不到 1 分钟 | 5 分钟 |

- **长回归只在可能受影响时运行。** 每个 PR 都会构建 wheel、核对版本标记，并把 CLI 与基线比对。同一个 job 里的 PostgreSQL 和 Lean 回归，在 PR 只改动 `web/`、`research_multiplier/workbench/app/`、`docs/`、`brand/`、`desktop/`、根目录的 Markdown 文件、issue 和 PR 模板、`tests/compat_validation/PACKAGE_VERSION` 或 `pyproject.toml` 的版本号这一行时跳过。改动其他任何路径都会运行；推送到 `main`、每周定时运行和手动运行也总会运行。job 摘要会写明本次是否运行。只有确认某个路径不会影响数据库边界或证明检查时，才把它加进这份清单。
- **卡住了就别等。** 一个 job 远远超过平时耗时、日志又没有新输出，就是卡住了。取消这次运行（`gh run cancel ID`），读日志（`gh run view ID --log`），然后修掉原因，或者重跑一次（`gh run rerun ID --failed`）。如果重跑又卡住，按下面的事故复盘处理，修好原因再合并。
- **运行环境里已有的东西不要再下载。** 优先用 runner 镜像自带的工具（例如端到端测试用镜像里的 Google Chrome），并固定版本，不要在运行时下载。
- **全绿才合并。** 有必需的检查还在等待或已失败时，不要合并；也不要为了通过而关闭、跳过或放宽检查。时好时坏的检查要开 issue 修好。

## 工程事故复盘

工程流程出了问题、耽误了时间或可能再次发生时（CI 卡住或失败、发布出错、合并出错、工作丢失、密钥泄露），在一天之内用 **Incident review** 模板开一个 issue。写得简短，对事不对人。它和研究上的[可信事件](trust-incidents.md)是两回事，后者针对已记录的核验结果。

| 部分 | 写什么 |
| --- | --- |
| 发生了什么 | 一两句话，日期和时间用 UTC |
| 影响 | 什么被耽误、出错或面临风险，持续了多久 |
| 发现 | 怎么发现的，怎样能更早发现 |
| 根本原因 | 底层原因，不只是触发点 |
| 修复 | 修复它的 PR 或提交 |
| 后续 | 防止再次发生的事项，各开一个 issue：测试、CI 检查或 `AGENTS.md` 里的规则 |

修复合并、每个后续事项都有了自己的 issue 之后，关闭这条复盘。反复出现的原因写进下一次[工程审计](#工程审计)。

## 发布

按[发布准备](release.md)执行，另外：

- 更新日志先根据已合并的 Conventional Commits 标题起草，再改写成用户看得懂的说明。
- 发布说明列出：
  - 数据库版本：从哪个版本升级而来，升级到哪个版本；
  - 支持的 Python、PostgreSQL 和 Lean 版本；
  - 接口版本的变化：API、插件协议、事件；
  - 本次废弃的功能。
- 打标签之前，中英文文档都要更新完。
- 从 P6 开始，发布文件带签名，并公布 SHA-256 校验值。

---

## 开放社区

- **行为准则：** 采用 Contributor Covenant，放在 `CODE_OF_CONDUCT.md`。
- **安全：** `SECURITY.md` 提供私下报告漏洞的渠道；确认的问题先修复，再公开。
- **许可证与签名：** 使用 Apache-2.0，配合开发者原创声明（DCO）（E9）。每次提交都带 `Signed-off-by: 姓名 <邮箱>`（用 `git commit -s`），CI 拒绝含有未签名提交的 PR。合并第一个外部 PR 之前再最后确认一次许可证，因为之后再换需要所有贡献者同意。
- **代码负责人：** 用 `CODEOWNERS` 按模块指定评审人，模块包括内核、迁移、web、Lean、插件。迁移、安全相关代码和 `_pinned/` 的改动，一律要维护者评审。
- **治理：** 待定的架构问题和发布由维护者决定。新决定的提议以修改 `architecture.md` 的 PR 形式提出。
- **收费扩展（如果以后做成产品）：** 放在明确分开的目录或仓库里，使用自己的许可证；Apache-2.0 的核心部分绝不依赖它们。
- **许可证标识：** 新建的源文件第一行写 SPDX 标识，例如 `# SPDX-License-Identifier: Apache-2.0`。`_pinned/` 下的文件保持已记录的字节不变，不为加标识而修改（B7）。
- **名称与标志：** 遵守 [TRADEMARKS.zh-CN.md](../TRADEMARKS.zh-CN.md)；分叉版本和托管服务使用自己的名字。
- **第三方许可证：** 新依赖的许可证必须与本项目的许可证兼容。CI 按允许清单检查 Python 和 npm 依赖的许可证。Mathlib 采用 Apache-2.0，保留它的版权声明。
- **新手友好：** 保持有几个范围小、说明清楚的 issue，打上 `good first issue` 标签。

---

## 贯穿始终的考虑

设计任何功能时，都对照检查这几点：

- **安全**
  - 凭据和连接串绝不出现在日志、URL、事件或错误信息里。
  - 本机服务只监听本地回环地址，并且必须带 session 令牌访问。
  - 每次写操作都由服务端检查权限，不能只靠界面隐藏按钮。
- **隐私**
  - 没有授权，任何内容都不离开工作区（D4）。
  - 如果以后加入使用统计，必须由用户主动开启。
  - 日志只记录 ID，不记录研究内容。
- **可复现**
  - 证据记录它用到的每样东西的确切版本和哈希（B1、B7）。
  - 升级永远不改写过去的证据。
- **数据归属**
  - 研究者可以把一个工作区的记录和产出按文档规定的格式导出。
  - 备份必须能恢复，并在 `postgres` 测试线里实际测过。
- **国际化**
  - 界面文字来自翻译文件（E6）。
  - 日期、数字、复数按语言地区格式化。
  - 研究内容按用户输入原样保存，绝不偷偷机器翻译。
- **无障碍**
  - 达到 WCAG 2.1 AA，所有操作都能用键盘完成，并在 Playwright 测试里检查。
- **性能**
  - 界面首次加载的脚本压缩后不超过 300 KB。
  - 在测试数据库上，主要页面 300 毫秒内响应。
  - 繁重的计算放在 worker 或插件里，不放在请求处理函数里。
- **平台**
  - CLI 和本机工作台支持 Linux、macOS、Windows。
  - Lean 相关流程支持 macOS 和 glibc Linux，与现有文档一致。
  - 组内服务器面向装有 Docker 的 Linux。
- **成本控制**
  - 模型调用遵守项目和工作区的预算。
  - 到达上限就停止派发任务，而不只是给个警告。
- **可观测性**
  - 结构化日志，带请求 ID 和任务 ID。
  - 服务端和 worker 都提供健康检查接口（P3.5）。
