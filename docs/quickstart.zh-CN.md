# Research Multiplier 快速入门

[English](quickstart.md) · [完整文档](README.md)

## 安装

需要 Python 3.10 或更新版。先在软件源码目录之外创建虚拟环境。macOS 和 Linux 可以这样做。

```sh
RM_ENV="$HOME/.venvs/research-multiplier"
python3 -m venv "$RM_ENV"
. "$RM_ENV/bin/activate"
python -m pip install "/path/to/research_multiplier-<version>-py3-none-any.whl"
```

安装包在仓库的 Releases 页面，命令中的路径用自己下载的文件替换。从 Git 源码安装可按[源码快照安装方法](quickstart.md#install)操作，构建文件会留在临时目录。Windows 离线试用可按同一页的 PowerShell 安装步骤操作，并以 `& $RmPython -m research_multiplier` 代替以下命令里的 `research-multiplier`。

## 用自己的问题开始

在本地工作台里，可以先用「和 AI 梳理资料」整理选中的资料，也可以用「手动添加定理」自行录入。参见[课题开始与定理录入](project-intake.zh-CN.md)。

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

在同一发行版本的 Releases 资产中下载 `research_multiplier-<version>-first-proof.tar.gz` 和 `SHA256SUMS`，校验 SHA-256 后解压到软件目录之外，打开 `first-proof/README.md`。教程从安装开始，走过确认命题和前提、核验实际依赖、跨题复用和撤回，全程零模型调用。

在 macOS 或 glibc Linux 上准备本机 Lean 4.19.0 和带 pgvector 的 PostgreSQL 18，选一个空项目目录及空数据库。教程会带你安装 PostgreSQL 支持并绑定工作区。[数据库和项目设置](research-foundation.md#set-up-the-workspace)

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

用 [`lean reuse-theorem`](lean-core.md#replay-a-native-lean-theorem) 在同一工作区的另一课题中重放已采纳的 `.lean` 或 Lake 定理。程序以目标课题已确认的依据证明原定理所需的前提，并列出仍缺的依据。

[直接核验自己的 `.lean` 证明或本机 Lake 项目中已编译的定理](lean-core.md#check-a-lean-source-file-or-compiled-declaration)。核验使用已经确认的命题和依据，全程无需调用模型。

[在同一个已确认命题里自动核验、入库和复用中间定理，再核验原命题](lean-core.md#derive-and-reuse-local-lemmas)。

可以离线检索 Formalpedia 的公开快照。对选定结果，可用冻结命题和已确认依据重放，并在 Lean 通过后自动入库工作引理。[Formalpedia 检索和重放](literature-search.md#search-formalpedia-locally)

回到一个已有 PostgreSQL 工作区时，可以运行 `research-multiplier research resume` 查看已确认的范围、当前结论、进展和待决定事项。用 [Lean 证明路线](lean-core.md#check-sufficient-conditions-for-the-frozen-goal)登记待证的中间命题。公共模型搜索的[调用额度和历史](lean-search-budget.md)随冻结目标保存。

## 打开本机证明工作台

选好已经绑定 PostgreSQL 的项目目录，运行下面的命令。浏览器会打开该项目的证明图谱，以及和模型的对话。

```sh
research-multiplier --workspace "PATH" workbench
```

点定理或假设可查看已记录的 Lean 陈述、实际引用和证明卡点。长陈述可展开。「撤回影响」显示撤回已确认版本时哪些记录需要重验。图可以拖动和缩放，各区域之间的边界也可以拖动调整。选中节点后，下方的对话区显示这个节点上模型的进度和反馈。启用数据库版本 `0070` 后，已确认 Lean 陈述的私有课题还可以提交意见，由获授权的本机模型在下一次可用调用时读取。公开课题可选择启用 `0071`，在已批准的公开推进策略下逐条确认发送给 Codex。 [工作台说明](proof-workbench.md)

## 选用模型

显式请求可选的远程模型建议时，`run --model --base-url` 会将问题文件和检查结果发给所选端点。费用由所选端点决定。项目级授权和预算适用于绑定工作区中的模型入口。未发表研究使用已绑定工作区中明确授权的可信本地模型入口。[模型建议说明](quickstart.md#optional-model-suggestions) · [本地模型配置](model-adapters.md#work-with-restricted-research-in-the-terminal)
