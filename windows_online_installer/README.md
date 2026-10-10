# Windows 在线安装器

把整个项目目录解压到本地后，依次双击：

1. `install_online.bat`：安装或更新隔离运行环境。
2. `run_installed_gui.bat`：启动 Web GUI 并自动打开浏览器。
3. `uninstall_online.bat`：不再使用时完整卸载。

安装器会自动完成：

- 在 `%LOCALAPPDATA%\AIKaraokeWebGUIOnline` 创建独立的 Python 3.10 环境。
- 安装 FFmpeg、Git、当前项目依赖以及与硬件匹配的 PyTorch 运行时。
- 检测 NVIDIA 显卡，并依次尝试当前受支持的 CUDA wheel；无 NVIDIA 显卡时安装 CPU 版。
- 复制最新应用文件，清理旧版扁平模块、废弃后端依赖和本项目缓存中的旧 Qwen 模型，并执行编译与导入自检。
- 把模型和依赖缓存保存在安装目录的 `cache`、`pip_cache` 中。

安装不会修改系统 PATH，也不会注册全局 Conda 环境。首次处理歌曲时会下载当前固定链路所需的模型，因此仍需联网并预留足够磁盘空间。

如果安装中断，可以直接重新运行 `install_online.bat`；脚本会复用环境和缓存。卸载器会停止本项目的 Python Web 进程，并删除整个安装目录。
