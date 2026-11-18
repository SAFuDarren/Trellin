# macOS 版 Trellin

Trellin 在 macOS 原生窗口中打开 Research Multiplier 工作台。本地构建面向使用 Apple Silicon 的 macOS 15 及以上设备。应用自带 Python 和工作台。初次使用前，按[课题设置指南](research-foundation.zh-CN.md)准备本机 PostgreSQL、pgvector 和 Lean。课题目录由你选，放在软件目录外。

## 使用 Trellin

1. 把 `Trellin.app` 放进系统或个人的「应用程序」目录，然后从 Launchpad 打开。
2. 开始新项目时点「新建课题…」，选择存放位置，输入文件夹名称，确认「创建课题」。Trellin 会在这里建立新子目录，并在装有 pgvector 的本机 PostgreSQL 18 上建独立数据库。存放位置可以已有研究资料。如果通过「打开课题目录…」选了普通目录，也可以用「在此新建课题…」开始。
3. 用「文件 → 打开课题目录…」或 **⌘O** 切换到已初始化的课题。应用读取该课题保存的数据库连接设置。若以前只在命令行设置过连接变量，请先用现有设置流程保存课题的数据库登录信息。
4. 选择图谱节点。关闭窗口或按 **⌘Q** 退出后，再打开会回到上次成功打开的课题。原节点仍存在时会恢复选中状态。图谱、核验状态和待审事项会从该课题重新读取。
5. 如果打开或恢复课题失败，Trellin 会显示该目录，并区分缺少登录信息、设置文件无法读取和数据库连接失败。可用「打开课题目录…」选择其他目录。数据库暂时离线时，等服务恢复后点「重试」。「团队」中的本机安装检查会显示 Lean 和数据库的详细状态。数据库需要升级时，按[工作台设置说明](proof-workbench.zh-CN.md)操作。

新建时默认使用本机 `/tmp` 下的 Unix 套接字和 `5432` 端口，设置时尝试使用当前 macOS 用户或 `postgres`。其他本机安装的启动器可提供 `RM_DESKTOP_PG_SOCKET` 和 `RM_DESKTOP_PG_PORT`。确认存放位置和文件夹名后才创建数据库，课题登录信息由既有设置流程保存。

应用在 macOS 偏好设置里保存上次成功打开的目录和图谱节点。研究文件和证明记录仍在各自课题。每次打开课题时，本机服务使用空闲端口和新的会话。审批和外部模型授权继续使用浏览器工作台的规则。数据库迁移和依赖安装由现有工具执行。

## 构建本地审阅版

使用装有 Xcode 命令行工具和 Git 的 Apple Silicon Mac。从包含已构建工作台的 Git 工作树运行，需要通过 HTTPS 下载运行环境和软件包。构建会核对运行环境和软件包的哈希。请预先建立软件目录之外的输出目录。

```sh
desktop/macos/build.sh /absolute/path/to/local-review
```

输出经过本地临时签名的 `Trellin.app`。在源码目录运行桌面自动检查和既有离线回归：

```sh
python -m pytest -m offline
desktop/macos/tests/run-swift-tests.sh
```

交互验收时，用[本机 Lean 和 PostgreSQL](research-foundation.zh-CN.md)准备独立的公开课题。从 Launchpad 打开应用，选课题和一个非默认图谱节点，关闭后再打开。检查是否恢复同一节点和已保存的核验结果。切换到另一个独立课题后，查看其命题是否独立。取消选择和选中空目录都应保持目录内容原样。
