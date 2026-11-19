# macOS 版 Trellin

Trellin 在 macOS 原生窗口中打开 Research Multiplier 工作台。本地构建面向使用 Apple Silicon 的 macOS 15 及以上设备。应用自带 Python 和工作台。初次使用前，按[课题设置指南](research-foundation.zh-CN.md)准备本机 PostgreSQL、pgvector 和 Lean。课题目录由你选，放在软件目录外。

## 使用 Trellin

1. 把 `Trellin.app` 放进系统或个人的「应用程序」目录，然后从 Launchpad 打开。
2. 点「打开文件夹…」，选自己的研究目录。首次打开会在里面建立 `Trellin` 子目录，并在装有 pgvector 的本机 PostgreSQL 18 上建独立数据库，然后进入工作台。再次选同一目录会打开已有课题，也可以直接打开已初始化的 Trellin 课题目录。
3. 用「文件 → 打开文件夹…」或 **⌘O** 切换目录。应用读取该课题保存的数据库连接设置。若以前只在命令行设置过连接变量，请先用现有设置流程保存课题的数据库登录信息。
4. 选择图谱节点。关闭窗口或按 **⌘Q** 退出后，再打开会回到上次成功打开的课题。原节点仍存在时会恢复选中状态。图谱、核验状态和待审事项会从该课题重新读取。
5. 如果打开或恢复课题失败，Trellin 会显示该目录和原因。已占用 `Trellin` 名称的项目会留在原处，可用「打开文件夹…」选择其他目录。数据库暂时离线时，等服务恢复后点「重试」。「团队」中的本机安装检查会显示 Lean 和数据库的详细状态。数据库需要升级时，按[工作台设置说明](proof-workbench.zh-CN.md)操作。

首次打开时默认使用本机 `/tmp` 下的 Unix 套接字和 `5432` 端口，设置时尝试使用当前 macOS 用户或 `postgres`。其他本机安装的启动器可提供 `RM_DESKTOP_PG_SOCKET` 和 `RM_DESKTOP_PG_PORT`。课题登录信息由既有设置流程保存。

应用在 macOS 偏好设置里保存上次成功打开的目录和图谱节点。研究文件和证明记录仍在各自课题。每次打开课题时，本机服务使用空闲端口和新的会话。审批和外部模型授权继续使用浏览器工作台的规则。数据库迁移和依赖安装由现有工具执行。

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

用签名 App 检查启动和课题恢复。交互验收时，用[本机 Lean 和 PostgreSQL](research-foundation.zh-CN.md)准备一次性目录。从 Launchpad 打开应用，先取消选择，检查目录下没有 `Trellin`。再打开该目录，核对新子目录和数据库，然后重新选择该目录确认打开的是同一课题。在已有图谱的独立公开案例中另选一个非默认节点，重开后核对节点和已保存的核验结果。

退出后，对同一个 App 重新检查签名。

```sh
codesign --verify --deep --strict /absolute/path/to/local-review/Trellin.app
```
