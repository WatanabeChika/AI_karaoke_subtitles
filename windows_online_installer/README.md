# Windows Online Installer

双击入口：

- `install_online.bat`  
- `run_installed_gui.bat`  
- `uninstall_online.bat`

安装行为：

- 安装目录固定为 `%LOCALAPPDATA%\AIKaraokeWebGUIOnline`
- 运行时环境、依赖缓存、应用文件全部写在该目录内
- 运行时模型缓存写入 `%LOCALAPPDATA%\AIKaraokeWebGUIOnline\cache`
- 不修改系统 PATH、不注册全局 Conda 环境
- 不创建桌面快捷方式
- 如因网络等原因使安装中断，则先卸载再重新安装比较保险

卸载行为：

- 停止该项目启动的 `web_gui.py` 进程
- 删除桌面快捷方式 `AI Karaoke Web GUI.lnk`
- 删除整个 `%LOCALAPPDATA%\AIKaraokeWebGUIOnline` 目录
