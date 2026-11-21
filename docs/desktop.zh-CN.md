# macOS 版 Trellin

Trellin 在 macOS 原生窗口中打开 Research Multiplier 工作台。本地构建面向使用 Apple Silicon 的 macOS 15 及以上设备。应用自带 Python 和工作台。初次使用前，按[课题设置指南](research-foundation.md)准备本机 PostgreSQL、pgvector 和 Lean。课题目录由你选，放在软件目录外。

## 使用 Trellin

1. 把 `Trellin.app` 放进系统或个人的「应用程序」目录，然后从 Launchpad 打开。没有上次打开的课题时，Trellin 会在 `~/Library/Application Support/Trellin/Library` 新建并打开默认库。
2. 用「文件 → 新建课题…」在库里建独立课题，用「文件 → 打开课题…」切换已登记课题。每个课题有各自的 ID、文件和 PostgreSQL 绑定。
3. 用「文件 → 打开文件夹…」或 **⌘O** 打开指定位置的课题。可以直接选择已初始化的课题目录。选择普通研究文件夹时，Trellin 新建或打开该文件夹内专属的 `Trellin` 子目录。原始研究文件保留在原位置。此前只用环境变量配置的课题，请先用已有设置流程保存数据库登录信息。
4. 选择图谱节点，再按 **⌘Q**。下次会恢复上次成功打开的课题，原节点仍存在时也会恢复。已有课题从登记位置打开。
5. 数据库暂时离线时，服务恢复后点「重试」。应用使用装有 pgvector 的本机 PostgreSQL 18，默认是 `/tmp` 下的 Unix 套接字和 `5432` 端口。其他本机安装可通过 `RM_DESKTOP_PG_SOCKET` 和 `RM_DESKTOP_PG_PORT` 指定。数据库升级按[工作台设置说明](proof-workbench.md)操作。

在工作台任一页面按 **⌘K**（其他平台用 **Ctrl+K**），或点击左侧搜索按钮，打开「命令面板」。可以搜索当前项目的课题标题、`FLOW-09` 这样的完整引用编号，以及文库条目、对话和 Agent。最近访问的结果排在前面。命令面板也能打开「添加定理」「用 AI 整理」「研究引用」、当前选中课题的「证明任务」，或发起新对话。需要提交的操作在对应面板里确认。

在项目图谱点「查找课题」搜索并选中卡片。在「Agent 对话」的选择器里搜索接收方或对话。在这些搜索框和命令面板中，用方向键移动，按 **Enter** 确认选择，按 **Esc** 返回原控件。

右键点项目图谱的卡片或证明节点，就能看到对应的操作。聚焦后按 **Shift+F10** 也可打开菜单。「写证明意见」会打开该节点的编辑框，「在文库中查看」会显示该课题已记录使用的来源。

在[「Team → 运行环境」](project-intake.zh-CN.md#连接模型)选择已安装的模型。集中部署如需使用非标准目录下的命令，可在 Trellin 的启动环境中用 `RM_DESKTOP_TOOL_PATH` 指定工具目录。目录必须是绝对路径，以冒号分隔。

「证明活动」提示课题库版本较旧时，点「备份并升级」，核对 Mac 对话框中的课题路径。Trellin 先验证包含课题文件和 PostgreSQL 快照的本地备份，再升级此课题的数据库并重新打开课题。请保留备份供恢复使用。升级需要此课题已保存的数据库所有者登录信息。

## 存储管理

从 macOS 菜单点「Trellin → 设置… → 存储」，或按 **⌘,**，查看当前库的位置并在 Finder 中打开。点「迁移数据…」选择新位置的上级文件夹。Trellin 会在预览显示当前路径和新建的 **Trellin Library** 目录。确认后先复制并核对文件、课题 ID 和数据库绑定，再切换登记位置。旧库保留为恢复副本，迁移中断或校验失败时原库继续可用。已管理课题的新路径可从「打开课题…」进入。

迁移覆盖库和其中集中管理的课题文件。外置课题和显式导出的文件保留在原位置。PostgreSQL 继续使用原有数据位置和绑定。应用支持目录保存精简的位置记录和应用设置，macOS 偏好保存上次打开的课题，数据库登录保留在受保护的配置中。

已有的[课题备份](research-foundation.md#recover-and-monitor)可同时保全课题文件和 PostgreSQL 快照。备份应用设置时，另外保存应用偏好及已登记的库位置。课题归档也可以恢复为独立课题。

在「设置 → 软件更新」可以查看已安装版本，也可以手动检查是否有新的稳定版。自动检查默认关闭。开启后，工作台运行期间每 24 小时最多检查一次。检查只向 GitHub 请求公开的版本信息，不发送课题数据或凭据。你可以在浏览器中打开更新说明，自行决定何时安装更新。

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

## 核验签名构建

在软件目录和 App 之外新建虚拟环境，运行[已安装 wheel 的兼容回归](../tests/compat_validation/README.md)。安装与 `tests/compat_validation/PACKAGE_VERSION` 一致的 wheel，课题、数据库和日志都使用临时位置。若 Python 或软件包来自 macOS App 内部，回归入口会在启动测试前拒绝执行。

用签名 App 和独立的应用配置，从 Launchpad 在[本机 Lean 和 PostgreSQL](research-foundation.md)上验收。确认首次打开自动建库，重开后恢复到同一课题。用一个库内公开课题和另一个外置公开课题核对两条独立数据库绑定及核验记录。在存储设置预览并迁移到未占用的位置，再打开两个课题，比对 ID、证据和文件。确认目标不可用时原库仍能打开。

退出后，对同一个 App 重新检查签名。

```sh
codesign --verify --deep --strict /absolute/path/to/local-review/Trellin.app
```
