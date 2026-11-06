# 历史会话供应商兼容启动

Codex 0.166 系列开始出现的供应商一致性校验，会阻止部分旧会话在 HUD 统一身份 `codex_hud` 下恢复。HUD 遇到原生供应商不匹配提示时，会提供「手动兼容启动说明」，不再尝试改写禁止属性赋值的 RPC stub。

兼容入口固定使用 **0.165.0**，这是 166 之前最后一个稳定版。2026-11-06 使用 npm 发布的 Windows x64 包、核对 SHA-512 后，在临时配置和会话中验证：保存为 alpha 的会话在当前 beta 配置下执行 `thread/resume` 成功，保留原生 alpha 身份。该验证不调用真实供应商、不生成回复；完整 Desktop 交互及 macOS 运行效果仍需实际验收。此前已验证的 0.162.1 可继续通过旧 PowerShell 脚本启动，但不把所有 166 之前的版本视为已验证。

在提示中点击说明后，HUD 会生成适合当前 Windows/macOS 的两条命令：

1. `npm install --prefix <HUD运行目录>/compatible-core/0.165.0 --no-audit --no-fund @openai/codex@0.165.0`：安装到独立目录，不覆盖全局 CLI 或应用自带核心；首次需 Node.js/npm。
2. `codex-hud --provider-compatible-codex <安装目录>`：仅为新启动的 Codex 子进程设置 `CODEX_CLI_PATH`，保留 HUD 已使用的 CDP 端口。实际命令会按源码或安装包形态生成完整路径。

执行启动命令前，先等待任务完成、保存草稿并手动关闭所有 Codex 窗口；HUD 和独立代理可以保留。启动器发现 Codex 仍运行时会拒绝重复启动，不结束现有进程。它会验证核心实际版本必须为 0.165.0；`--check` 只检查路径、版本和运行状态，不启动应用。可用 `--app <可执行文件或.app路径>` 指定 Desktop。

源码环境也可以执行：

```text
python tools/start_provider_compatible_codex.py --provider-compatible-codex <安装目录> --check
python tools/start_provider_compatible_codex.py --provider-compatible-codex <安装目录>
```

兼容选择仅影响这次启动，下次从系统正常打开 Codex 时恢复使用应用自带核心。不会修改会话原生身份、供应商配置、全局环境变量或 HUD 网关开关。希望继续使用新版核心时，也可关闭 HUD 请求代理、选择原会话供应商后重试。
