# codex-usage-hud v1.3.1

修复 Windows Store 版 Codex 经 HUD 启动时出现“ChatGPT failed to start / 该进程没有程序包标识符”的问题。

- 改用 Windows 程序包激活接口启动 Store 版 Codex，保留程序包身份。
- 保留 Renderer HUD 所需的 CDP 调试参数。
- 普通 Windows 安装版和 macOS 启动方式保持不变。

下载 Windows 安装程序进行升级；同页提供 SHA-256 校验文件。
