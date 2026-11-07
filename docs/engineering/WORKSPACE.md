# 本机工作空间与桌面维护

在「运行管理 → 运行环境」可检查当前项目、会议数据、备份、Python 和本机模型路径。路径预检按文件状态识别云端占位。Whisper 和 Qwen 的就绪状态仍由各自引擎检查。

`MEETING_DATA`、`MEETING_BACKUPS`、`MEETING_MODELS` 分别指定数据、备份和本机模型目录。单个模型可继续用 `WHISPER_MODEL`、`QWEN_ASR_MODEL`、`QWEN_ALIGNER_MODEL` 等原有变量覆盖。模型路径变量要传给实际启动的服务进程。源码由 Git 管理，迁移源码后应按依赖重建 Python 环境并重新编译含绝对链接路径的本机组件。

数据迁移按以下顺序进行。示例中的备份和目标目录必须是全新、相互独立的目录。先完成录音与任务，并同步浏览器暂存。

```sh
./workspace.command check
./workspace.command plan --backup /local/backups/move-1 --target /local/new-data
./service.command stop
./service.command status
./workspace.command stage --backup /local/backups/move-1 --target /local/new-data
./workspace.command verify-target --backup /local/backups/move-1 --target /local/new-data
MEETING_DATA=/local/new-data ./service.command start
```

等待原服务确认停止后执行 `stage`。它锁定源目录，通过一致性备份、逐文件校验及数据库核验生成可运行的副本。切换前保持源服务停止，避免预演后又在源目录产生新内容。原生宿主使用「连接设置」保存新数据目录，先核对旧连接已停止，再启动新连接。切换后确认历史会议和录音可打开。若需回退，先停止新目录服务，并单独保全切换后产生的数据，再把数据路径设回原目录。

桌面维护使用以下入口。源码或前端更新后先构建前端，再结束录音与任务、停止服务并退出宿主。命令通过现有签名、租约和发布事务检查，宿主与采集器各有独立回退记录。

```sh
./workspace.command desktop-status
./workspace.command desktop-build --component host
./workspace.command desktop-build --component capture
./workspace.command desktop-rollback --component host --release RELEASE_ID
./service.command start
./workspace.command desktop-status
```

回退编号可从状态报告查询。宿主仍与本机源码和 Python 环境绑定。需要回退源码或数据结构时，按相应版本的运行要求和离线备份流程操作。
