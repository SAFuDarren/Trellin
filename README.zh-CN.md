# Research Multiplier

**人和 AI 模型一起证明定理的研究工作台，每一步都由 Lean 核验。**

[English](README.md) · 简体中文

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/workbench-dark.png">
  <img alt="Research Multiplier 工作台上的费马大定理示例项目：证明图中，归约到奇素数指数以及 n = 3、n = 4 两种情形已核验，p ≥ 5 的情形仍待证明，两个模型正在处理 Frey 曲线这一步" src="docs/images/workbench-light.png">
</picture>

<sub>费马大定理示例项目。归约到奇素数指数，以及 n = 3 和 n = 4 两种情形，都是 Mathlib 里的定理。一般情形在这里仍待证明：它经由 Frey 曲线的路线依赖模性定理和 Ribet 定理，这两者 Mathlib 里还没有，所以记为假设。图中是示例数据，可以用 <code>python tests/fixtures/workbench_demo_fermat.py</code> 自己运行。</sub>

## 它能做什么

证明什么、命题的确切含义，都由你决定并确认。之后，Research Multiplier 保证证明过程经得起检查。

- **冻结确切的命题。** 每个课题都固定为一条 Lean 陈述，连同你允许使用的假设和定理库定理。没有经过你确认的陈述，不算被证明。
- **把整个证明看成一张图。** 目标、归约、待证义务、假设和定理库定理，哪些已核验、计划中、卡住了、等你审阅，一目了然。
- **让模型分担繁重的部分。** 模型在你设定的预算内并行为待证步骤提出证明。你可以看到它们的思路，并在任意节点上留下意见。每个结果都由 Lean 核验，不凭模型一句话就采信。
- **清楚依赖关系。** 撤回一个假设之前，工作台会列出所有需要重新核验的结果。
- **复用已经证明的结果。** 已核验的引理和定理库定理连同确切版本一起记录，之后的课题可以直接重放。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/withdrawal-dark.png">
  <img alt="「撤回影响」视图：撤回模性与降级假设后，Frey 曲线路线、p ≥ 5 的情形和归约都标记为需要重新核验" src="docs/images/withdrawal-light.png">
</picture>

<sub>「撤回影响」预览后果：如果没有模性定理和降级定理，Frey 曲线路线、p ≥ 5 的情形和归约都需要重新核验。在你决定之前，不会改动任何记录。截图中的界面为英文，界面可在设置中切换为中文。</sub>

## 开始使用

| 如果你想… | 从这里开始 |
| --- | --- |
| 用自己的问题试一试 | [离线快速入门](docs/quickstart.zh-CN.md)。只需要 Python 3.10 或更高版本，就能保存研究简报并重放支持的检查。 |
| 核验第一个 Lean 证明 | [第一个证明的完整流程](docs/quickstart.zh-CN.md)：确认定理和假设、核验、在第二个课题里复用，再撤回。需要 Lean 4.19.0，以及带 pgvector 的 PostgreSQL，支持 macOS 或 glibc Linux。 |
| 检查已有的证明 | [检查 `.lean` 文件或 Lake 声明](docs/lean-core.md#check-a-lean-source-file-or-compiled-declaration)，查看它实际用到的假设和定理。 |
| 打开工作台 | 有 PostgreSQL 数据库的项目可以打开[本地证明工作台](docs/proof-workbench.md)：`research-multiplier --workspace PATH workbench`。 |

[完整文档](docs/README.md)（英文）

## 工作方式

- **本地优先。** 每个研究项目都有自己的文件夹和 PostgreSQL 数据库。输入、证明和结果都留在那里，除非你授权，否则不会离开工作区。[工作区结构与备份](docs/research-foundation.md#workspace-paths)
- **核验，而不是信任。** 证明由 Lean 4 和固定版本的 Mathlib 核验；部分其他领域由确定性检查器覆盖。核心核验和重放完全不需要调用模型。
- **模型是可选的。** 可以接入本地模型、Codex CLI 或 API 服务，花费受你批准的项目月度预算约束。[模型适配器](docs/model-adapters.md)
- **为扩展而设计。** Lean 是第一个插件；核心与领域无关，插件以独立进程运行，可以用任何语言编写。[架构](docs/architecture.zh-CN.md)

## 项目状态

Research Multiplier 正在积极开发，准备首次公开发布。[路线图](docs/roadmap.zh-CN.md)列出了各项任务和验收标准，[更新日志](CHANGELOG.md)记录每个版本新增的内容。

## 参与贡献

欢迎贡献。请阅读[贡献指南](CONTRIBUTING.md)和 [`AGENTS.md`](AGENTS.md)；提交需要按开发者原创证书（DCO）签名。

## 许可证

程序和文档采用 [Apache License 2.0](LICENSE) 授权，另见 [NOTICE](NOTICE)。你的研究数据、证明和结果归你所有，不受本许可证约束。项目名称和标志的使用遵循[名称与标志政策](TRADEMARKS.md)。
