# Trellin 快速入门

[English](quickstart.md) · [完整文档](README.md)

## 安装

桌面版见 [Trellin for macOS](desktop.zh-CN.md)。命令行从源码安装需要 Git 和 Python 3.10 或更新版。在 macOS 或 Linux 上，可在软件目录外创建虚拟环境并安装。

```sh
RM_ENV="$HOME/.venvs/research-multiplier"
python3 -m venv "$RM_ENV"
. "$RM_ENV/bin/activate"
python -m pip install "git+https://github.com/SAFuDarren/Trellin.git"
research-multiplier --help
```

已有对应版本的 wheel 时，可以直接把它的本地路径传给 `python -m pip install`。Windows 离线试用可按[英文版的 PowerShell 步骤](quickstart.md#install)安装，并用 `& $RmPython -m research_multiplier` 代替下文的 `research-multiplier`。

## 用自己的问题开始

在本地工作台里，可以用「研究操作 → 和 AI 梳理资料」整理选中的资料，也可以用「手动添加定理」自行录入。参见[课题开始与定理录入](project-intake.zh-CN.md)。

先运行选址命令，输入你为这个课题选的目录。程序会打印实际地址。在下列命令中把 `PATH` 换成该地址后进入项目。PowerShell 用 `Set-Location "PATH"`。

```sh
research-multiplier research init-workspace
cd "PATH"
research-multiplier research paths
research-multiplier init inputs/question.json
```

用文本编辑器打开 `inputs/question.json`。在 `title`、`question`、`success` 里填写标题、想弄清的问题和什么算进展。可以用中文，`kind` 保持为 `open`。

```sh
research-multiplier run inputs/question.json
```

开放问题会生成状态为 `needs_review` 的简报。终端会打印 `runs/offline/` 下的 `report.md` 和 `run.json` 路径。前者用于阅读，把后者传给 `research-multiplier replay` 可以重跑。想试确定性检查，可看[图着色和线性规划的输入格式](problem-formats.md#built-in-checks)。

已有选定的地址时，可以直接运行 `research-multiplier research init-workspace "PATH"`。从其他目录操作时，把 `--workspace "PATH"` 放在子命令前，文件参数使用绝对路径。[工作区布局](research-foundation.md#workspace-paths)

## 首次核验和复用一个 Lean 证明

先用 [Lean 指南](lean-core.md)的小例子确认和核验一个命题，全程无需模型。该指南也说明如何复用结果和撤回依据。Lean 命题只能使用一组固定的 Core 语法：`ℕ` 写成 `Nat`，`[Fintype X]` 写成 `(iX : Fintype X)`，`|a - b|` 写成 `abs (a - b)`，`∑ C ∈ M, f C` 写成 `Finset.sum M f`，`x ∈ s` 写成 `Membership.mem s x`，`∅`、`⊥` 写成 `EmptyCollection.emptyCollection`、`Bot.bot`，`Type*` 写成 `Type`。`intake preview-target` 会在冻结前指出其他写法和对应改写。需要更丰富语法的定义请放进自己的 Lean 项目，再按名称引用。[可接受的命题语法](lean-core.md#accepted-proposition-syntax)已有匹配发行包时，用 `SHA256SUMS` 核对其中的 `first-proof` 压缩包，在软件目录外解压后查看完整教程 `first-proof/README.md`。

在 macOS 或 glibc Linux 上准备本机 Lean 4.19.0 和带 pgvector 的 PostgreSQL 18，选一个空项目目录及空数据库。按下面的设置指南安装 PostgreSQL 支持并绑定工作区。[数据库和项目设置](research-foundation.md#set-up-the-workspace)

想一步准备好持久研究和 Lean，对项目目录运行 `setup`。它使用你本机已有的、带 pgvector 的 PostgreSQL 18 服务器；缺少的软件和大文件下载，都会先问你同意才安装。[setup 做了什么](research-foundation.md#set-up-in-one-command)

```sh
research-multiplier setup "PATH"
```

想知道还缺什么，在项目目录里运行自检。它只读取当前环境，不做任何改动。每个需要处理的项目后面都附有修复命令或要设置的变量，最后汇总离线使用、持久研究和 Lean 核验是否已经可用。脚本或 agent 调用时加 `--json`。

```sh
research-multiplier doctor
```

想让 Claude Code、Codex 这类 AI 编程助手帮忙，就在项目目录里启动它。`research init-workspace` 和 `setup` 会在项目目录写一份 `AGENTS.md` 指南，外加一个指向它的 `CLAUDE.md`。指南告诉 agent 哪些命令只读、哪些会把材料发给模型，以及所有需要你签字的决定都只能由你来做。你改过的副本不会被覆盖。`research-multiplier agent-guide` 打印当前版本的指南；升级后用 `agent-guide --install` 更新未改动过的副本。

## 继续自己的研究

需要保存命题、证明依赖和审阅记录时，先配置选定项目的 PostgreSQL 数据库。固定网络的最大流可以直接核验证书，也可以显式启用 Codex 和 Claude Code 做证明和独立审计。[工作区和数据库配置](research-foundation.md#set-up-the-workspace) · [固定网络流程](v0a.md)

如果只知道网络的结构和容量，可以先探索它的最大流候选值，再决定要证明哪条陈述。[探索流程](exploration.md)

[用已有的本机 Lean 项目验证自己确认过的命题](lean-core.md#check-a-goal-with-your-native-lean-project)。

冻结后发现无法推进的课题（例如冻结时写错了），可以用 `research withdraw-claim CLAIM_ID --actor 姓名 --reason 原因` 撤回；若已有修正后的课题替代它，加上 `--superseded-by 新课题ID`，两者会在历史中关联。撤回的课题不再出现在项目图谱和课题列表中，合同、证据和决定记录都会保留。`research withdrawn-claims` 列出已撤回的课题，`research restore-claim` 可以恢复。项目数据库需要升级到 `0084`，Trellin 打开旧项目时会提示升级。[撤回无法推进的课题](research-foundation.md#withdraw-a-claim-that-cannot-proceed)

使用 PostgreSQL URL 和新建的空库[恢复项目存档](research-foundation.md#recover-and-monitor)。若数据库导入后还有步骤失败，保留已导入的库和原存档，另选空库及尚未使用的目录重试。

在所选项目运行 `research-multiplier research latest-backup`，可找到最近通过完整性校验的机器备份。默认查看 `backups/`，也可用 `--directory` 指定外部备份目录。按清单文件的修改时间排序。有恢复需求时，另用空库验证存档能正常还原。

用 [`lean reuse-theorem`](lean-core.md#replay-a-native-lean-theorem) 在同一工作区的另一课题中重放已采纳的 `.lean` 或 Lake 定理。程序以目标课题已确认的依据证明原定理所需的前提，并列出仍缺的依据。

[直接核验自己的 `.lean` 证明或本机 Lake 项目中已编译的定理](lean-core.md#check-a-lean-source-file-or-compiled-declaration)。核验使用已经确认的命题和依据，全程无需调用模型。

[在同一个已确认命题里自动核验、入库和复用中间定理，再核验原命题](lean-core.md#derive-and-reuse-local-lemmas)。

可以离线检索 Formalpedia 的公开快照。对选定结果，可用冻结命题和已确认依据重放，并在 Lean 通过后自动入库工作引理。[Formalpedia 检索和重放](literature-search.md#search-formalpedia-locally)

回到一个已有 PostgreSQL 工作区时，可以运行 `research-multiplier research resume` 查看已确认的范围、当前结论、进展和待决定事项。用 [Lean 证明路线](lean-core.md#check-sufficient-conditions-for-the-frozen-goal)登记待证的中间命题。公共模型搜索的[调用额度和历史](lean-search-budget.md)随冻结目标保存。

## 导出可打印的科研交接

先用命题 UUID 预览精确范围。多条命题可重复 `--claim`，也可用 `--project` 预览当前课题中的命题和待判断事项。

```sh
research-multiplier research handoff --claim CLAIM_UUID --language zh-CN
research-multiplier research handoff --claim CLAIM_UUID --language zh-CN \
  --out exports/first-handoff --confirm SNAPSHOT_SHA256
```

把 `SNAPSHOT_SHA256` 换成预览返回的摘要。选中内容变化时需要重新预览。目标为 `exports/` 下或外部已有上级目录中的新文件夹。

可以离线打开并打印 `handoff.html`，也可以直接编辑 `handoff.md`。两份文件使用相同快照，列出命题、已冻结的假设、实际和计划依赖、核验与审批状态、来源、笔记与下一步。应用凭据和模型对话记录不在导出中。恢复完整项目仍用机器备份。 [英文操作说明](research-foundation.md#take-a-research-handoff-with-you)

## 打开本机证明工作台

选好已经绑定 PostgreSQL 的项目目录，运行下面的命令。浏览器会打开该项目的证明图谱，以及和模型的对话。

```sh
research-multiplier --workspace "PATH" workbench
```

「主页」直接打开导轨旁的「项目图谱」，底部的缩放控件旁会显示连接状态。回到图谱时，会恢复选中的课题和节点。「工作」包含「收件箱」「审阅」「课题」「Agent 任务」和「定理库」。导轨上的「工作」徽标显示等你处理的数量。导轨底部是「帮助」和「设置」，各点一下即可打开。

Mac 版导轨顶部的课题方块可以切换已登记的本地课题，也能新建课题。打开课题文件夹在「设置 → 高级」。在「项目图谱」单击课题会原地选中，并在右侧显示陈述、Lean 代码和待审记录。双击课题卡片、在聚焦的卡片上按 Enter，或在详情栏点「打开证明」，都可在主画布中查看证明图。详情栏把「证明任务」放在首位，空间不足时可在「更多操作」中找到其余入口。点「项目图谱」或按 Esc 会回到原来的位置和选择。窗口较窄且详情抽屉已打开时，第一次按 Esc 会先收起抽屉。点「全局图」可查看近期课题及其证明依赖。用「查找课题」定位，拖动卡片调整布局。把卡片底部的连接点拖到另一张卡片，或聚焦卡片后按 L，即可记录研究关联。选中线条即可移除关联。位置和研究连线按项目保存在本机。

已批准的 Lean 目标还没有候选时，可从「审阅」的「开放研究目标」打开。通过「相关工作」按关键词、标题或标识检索当前课题库，查看来源、命题和依据版本的记录状态。[本地目标检索](research-goals.md#find-related-work-in-the-project)

在「审阅」中打开「待比较的猜想」，可以比较已准备的 Lean 陈述，并核对已批准依据的版本、形式和来源。选定一项并填写理由后会打开确认的课题。依据撤回或解释待审时，先处理对应范围问题。[猜想审阅](research-goals.md#reopen-a-prepared-conjecture-in-workbench)

点定理或假设可查看已记录的 Lean 陈述、实际引用和证明卡点。长陈述可展开。「撤回影响」显示撤回已确认版本时哪些记录需要重验。图可以拖动和缩放，各区域之间的边界也可以拖动调整。选中节点后，在右侧展开「证明详情 → 证明活动」，查看该节点的进度和反馈。启用数据库版本 `0070` 后，已确认 Lean 陈述的私有课题还可以提交意见，由获授权的本机模型在下一次可用调用时读取。公开课题可选择启用 `0071`，在已批准的公开推进策略下逐条确认发送给 Codex。 [工作台说明](proof-workbench.md)

选中课题后打开「证明任务」，查看已确认的 Lean 命题、依据和待处理事项。范围就绪后，填写理由并授权启动。系统先做确定性检查和证明复用，默认允许零次模型调用。私有课题可以为已配置的本机 Lean 模型授权有限轮次。「停止」结束当前策略。重新打开工作区后可以查看原任务的进度和核验结果，工作中断时可以继续已授权的任务。证据的审阅和采纳仍由研究者决定。[证明任务说明](proof-workbench.md#start-a-proof-task)

选中已有确认 Lean 陈述的课题，打开「研究操作 → 本地引理」。核对陈述和版本，粘贴只含中间引理的 JSON 计划，再点「核验引理计划」。Lean 在本机核验，通过的引理会出现在证明图谱中。[本地引理说明](proof-workbench.md#verify-local-lemmas)

选中 Lean 课题，打开「前提」提交拟议前提。待审节点会显示在原图上，审阅期间会暂停该范围的新证明任务。核对原目标、已确认的起始依据和新前提后，可以拒绝提议或确认一个独立修订课题。原课题的陈述和证明记录仍可查看。修订课题需先确认自己的 Lean 陈述。[前提审阅](proof-workbench.md#propose-a-premise)

在 Mac 工作台的「Team → 运行环境」可选择已安装的 Codex 或 Claude Code 和具体模型，沿用工具自己的登录。资料梳理可以预览并确认要发送的公开摘录。数据库升级至 `0078` 后，还可以给已发布范围的公开证明任务指定连接和轮次上限，查看服务商回报的用量。[模型连接说明](project-intake.zh-CN.md)

在「团队 → 执行者」点「添加员工」，先选一个角色：Lean 证明助手、文献检索、独立审阅或研究规划，也可以「从空白开始」。角色会填好名字、说明、指令和开场白，并自动选用第一个可用的模型连接，你只需检查后点「创建」。还没有已保存的 Agent 时，「对话」左栏也会列出这些角色，创建后直接打开与它的对话。已保存的 Agent 会一直列在「对话」左栏，点选即可开始对话。资料保存在这台设备的当前项目中，可用「编辑」修改资料或更换模型连接。

数据库升级到 `0072` 后，可在「研究引用」里更改课题短码，或输入笔记里的编号打开对应定理。节点的序号固定，改短码后旧短码和节点编号仍可查找。[研究引用说明](proof-workbench.md#work-with-the-models)

在「工作 → 课题」选择「看板」「列表」或「表格」，展开课题可原地阅读陈述和 Lean 形式化。「工作 → 定理库」的表格和详情会显示条目状态。

数据库升级至 `0079` 后，打开「对话」，在左栏选员工，再点「新建任务」并命名。点顶部当前任务的标题即可切换任务，查看哪些任务在等待回复或发送失败。继续聊天时会使用员工已配置的连接，草稿会随各自的任务保留。点「对话」标题旁的「会话历史」时钟，可打开早期未分配的会话和证明活动。每页底部都可以打开「Agent 对话」。在「工作台」选中课题或证明节点后，可用「询问 Agent」带上对应上下文。

点击发送或按回车发送消息，Shift+回车可换行。发送前可点「+」再选「请求详情」，查看会发给谁以及附带的完整内容。「Team → 模型」和「Team → 运行环境」共用已保存的模型连接。

数据库升级至 `0083` 后，可在「Team → 员工」给已保存的 Agent 派工。填写任务目标和验收标准，预览准确的输入和操作范围，再确认启动。实现任务可选择项目 `inputs/` 内的 Git 工作区、完整提交编号和要改的文件。Trellin 会在临时目录验证补丁能否应用到选定版本。从成果上分派审查可传递同一版本。重开后可在「工作 → Agent 任务」找到待验收成果，并接受或退回。在「Team → 员工」点 Agent 名称，可查看该 Agent 在当前项目的活动、指令和模型连接，也可以直接派工或打开对话。

在「证明活动」里选定节点，点击「保存草稿」，重开课题后可以继续编辑。即使暂时无法排队，也可以保存私有草稿。需要数据库版本 `0082` 或以上。

升级到 `0081` 后，在「证明活动」选中节点和接收方，再点「将证明意见排队」或按回车，即可放行当前符合条件的意见，供之后获授权的 Codex 证明任务读取。每条意见旁会显示正在等待授权、当前受阻、陈述已变更，或已有回执表明意见已纳入一次尝试。[证明意见说明](proof-workbench.md#send-a-public-note-to-codex)

拖动图谱节点的右下角可调整宽度和高度。

数据库升级至 `0080` 后，可从「资料库」表格或右栏打开同一个版本编辑器。核对改动前后的文字和 Lean 陈述，再确认新版本。旧核验收据保存在原命题下。替换依据后，受影响的核验进入待重验。

升级到 `0073` 后，在「审阅」里批准或退回证据时只需填写理由，系统会记录绑定工作区的本机用户。历史审批保留原先记录的署名。[工作台说明](proof-workbench.md#read-a-graph)

升级到 `0074` 后，从命令行或工作台批准、退回证据时，数据库会随审批写入一条可续读事件。事件里记录编号和动作，审阅理由保存在原审批记录中。

打开的工作台会自动更新审阅列表、相应课题图谱和总览。连接中断后，会从上次处理的事件继续读取。断连期间和尚未接入事件的操作仍可通过定期刷新查看状态。

数据库版本 `0075` 把命题和依据确认、核验输入撤回、证明任务状态和采纳结论记到同一事件流里。撤回后需要重验哪些证明，仍由现有依赖规则决定。

使用新版工作台时，这些变化会自动更新对应的图谱、审阅列表和证明任务。即使你从命令行确认范围、撤回依据或运行证明任务，打开着的窗口也能看到结果。断线重连后会补读遗漏的事件。

数据库升级到 `0076` 并使用 Research Multiplier `1.0.15` 或更新版本后，澄清、Agent 会话和其他工作区改动也会自动更新到对应视图。连接中断时会定期检查变化，重连后补读遗漏事件。

使用数据库版本 `0077` 和 Research Multiplier `1.0.18` 或更新版本时，可在 Trellin 的「审阅」页保存澄清回答，再生成更新后的 Lean 陈述。核对原始措辞、陈述和共享参数后，确认该版本。私有资料只由配置好的私有本机模型处理，公开资料在发送前需要确认。保存回答后可以退出，下次打开从同一问题继续。[澄清流程](research-intake.md#answer-an-unresolved-interpretation)

## 选用模型

显式请求可选的远程模型建议时，`run --model --base-url` 会将问题文件和检查结果发给所选端点。费用由所选端点决定。项目级授权和预算适用于绑定工作区中的模型入口。未发表研究使用已绑定工作区中明确授权的可信本地模型入口。[模型建议说明](quickstart.md#optional-model-suggestions) · [本地模型配置](model-adapters.md#work-with-restricted-research-in-the-terminal)
