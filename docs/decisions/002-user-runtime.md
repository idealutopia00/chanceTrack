# ADR 002：将后台运行副本部署到用户目录

日期：2026-11-17。状态：已实施并验证。

触发：仓库位于 /Volumes 外接卷。launchd 启动时系统日志明确返回 kTCCServiceSystemPolicyRemovableVolumes denied，无法打开仓库中的 stdout 文件，进程退出 78。终端手动启动正常并不意味着后台任务具有同样的文件访问权限。

选择：源码保留仓库，安装器显式复制当前代码、锁文件和观察清单到 ~/Library/Application Support/ChanceTrack/runtime，创建独立虚拟环境。历史数据放同级 data 目录，日志放 ~/Library/Logs/ChanceTrack。后台任务不再依赖外接卷。无需改变系统权限或 sudo。

安装前卸载旧任务，安装后加载两个用户 LaunchAgent。--remove 只卸载任务，不删除历史数据或运行副本。修改源码与观察清单后必须重新安装；默认不是实时同步。开发命令与部署副本默认访问同一历史库。

代价：两份虚拟环境，多一次显式部署。避免后台直接追踪开发中的半成品。已有原始数据库只在新位置没有数据库时用 SQLite backup 迁移；不会覆盖个人历史。

验证：launchd 页面进程 running；HTTP GET / 返回 200；检查任务在 14:35 前正常返回“当前不在检查时段”；日志正常落盘。尚未实际经历 14:35 触发或电脑休眠恢复，不能据此宣称这些场景已实测。
