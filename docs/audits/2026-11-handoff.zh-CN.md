# 回归和仓库交接跟进

[English](2026-11-handoff.md) · 简体中文

本次跟进在 main 的 `2162666` 上复核[发现 A-202611-13](2026-11.zh-CN.md)。以下两项检查是 [#9](https://github.com/SAFuDarren/Trellin/issues/9) 的验收证据。交接使用了 [#31](https://github.com/SAFuDarren/Trellin/issues/31) 和 [PR #34](https://github.com/SAFuDarren/Trellin/pull/34) 更新后的入门资料。

## 压力测试 4

在待审源码的一次性副本里运行下列测试。每次只引入一个故障，运行对应测试，然后恢复文件。Python 3.14 下，注入前和恢复后均通过三项测试。每个故障都使对应测试因断言失败而失败。

| 注入的故障 | 拦住故障的回归 |
| --- | --- |
| 序列化带时区偏移的事件时跳过 UTC 归一化 | [事件流重放和通知](../../tests/offline/test_workspace_events.py) |
| 安装时覆盖研究者修改过的工作区指南 | [保留已编辑的指南](../../tests/offline/test_agent_guide.py) |
| 重开已移动的托管项目时跳过当前位置解析 | [打开已移动的项目](../../tests/offline/test_desktop_bootstrap.py) |

## 压力测试 6

新的 Agent 收到了仓库和 [#8](https://github.com/SAFuDarren/Trellin/issues/8) 的真实 Dependabot 配置任务。它从仓库指引找到认领流程，认领后从 main 建立独立工作树，生成带 DCO 签署的提交 `75df203`。它用仓库清单核对 YAML，通过 Ruff 和 254 项离线测试，然后将范围、证据和剩余验收写回原 issue，交接整合并释放认领。 [认领](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039797145) · [交接证据](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039840411) · [释放认领](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039843724)

Agent 找到了 Python、React 和原生 macOS 源码、工作台构建产物的生成入口、Python、浏览器和原生测试的位置，以及一次性输出与真实研究数据的落点。它也识别出开放或依赖中的 PR、尚未核验的合并、未保存的工作、进行中的 Git 操作和占用中的进程会阻止清理分支或工作树。这些信息均可从 [AGENTS.md](../../AGENTS.md) 和链接的[工程指南](../engineering.zh-CN.md)找到。

版本预检要求下一个补丁版本，记录表却仍写着只在发布 PR 递增。双语指南已把候选版本规则统一，AGENTS.md 也在整合步骤明确链接版本规则和预检入口。
