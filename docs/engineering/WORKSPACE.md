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

## 将旧备份导入独立版

在旧版结束录音、同步暂存并停止服务后，可用 `./backup.command create --data "$old_data" --output "$backup"` 生成备份。在独立版首次启动之前，预览备份并恢复到独立版的全新数据目录。

```sh
target="$HOME/Library/Application Support/cn.seeknow.tingji.host.standalone/data"
./backup.command preview "$backup" --target "$target"
./backup.command restore "$backup" --target "$target" --preview-id "<预览返回的 preview_id>"
```

预览列出备份来源、应用与数据库版本、文件类型、字节数和资料数量。备份若含已保存的模型配置，预览仅提示存在，恢复则保留备份里的配置和凭据。确认范围后再执行恢复。独立版包内也可调用相同命令。

```sh
runtime="/Applications/Tingji.app/Contents/Resources/PortableRuntime"
"$runtime/python" backend "$runtime/code/scripts/manage_backups.py" preview "$backup" --target "$target"
```

恢复成功后打开独立版，抽查历史文字稿、简记、纪要和录音。若独立版已有资料，使用一个独立的新工作区演练，目标设为该工作区的 `data` 子目录，启动宿主时通过 `TINGJI_HOST_STATE_DIR` 指定该工作区。恢复途中中断会留下带标记的目标目录，应用会拒绝打开。先确认恢复进程已退出，将半成品移到旁边保留，再重新预览和恢复。原备份可以继续使用。

## 手动更新与回退独立版

将新版 DMG 中的 `Tingji.app` 提取到单独目录。先结束录音和后台任务，再退出旧版宿主和后台。更新期间使用原工作区。录音、任务或数据库写入尚在进行时，更新命令会要求稍后重试。

```sh
PYTHONPATH=. .venv/bin/python scripts/update_standalone.py update \
  --app "$installed_app" --state "$workspace" \
  --candidate "$extracted_app" --backup "$new_backup"
```

命令核对签名与数据库兼容性，保存备份，再替换应用。打开新版检查历史会议、录音和模型。需要回退时，退出宿主并停止后台，用更新返回的 `release` 编号找回上一版本。

```sh
PYTHONPATH=. .venv/bin/python scripts/update_standalone.py rollback \
  --app "$installed_app" --state "$workspace" --release "$release"
```

若换包中断，使用同一命令入口的 `recover --app "$installed_app" --state "$workspace"` 核对换包结果。恢复完成后可重新启动相应版本。如果旧版无法读取升级后的数据库，将升级前备份恢复到新工作区，并单独保留升级后生成的资料。
