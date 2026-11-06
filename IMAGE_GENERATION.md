# 在 Codex 聊天中生图

先在 HUD 中开启“供应商请求代理”，并给当前会话使用的供应商配置图片模型，例如 `gpt-image-2`。此功能使用供应商已有的地址、API key 和代理设置。

打开聊天输入框的原生模型选择器，直接选择当前供应商的图片模型。随后发送文字描述，每次生成一张 1024×1024 图片，并在聊天回复中展示。选回对话模型即可恢复普通聊天，其他会话和全局默认供应商不受影响。

新建会话还没有会话 ID 时也可以先选择图片模型。HUD 按每次请求携带的模型决定是否生图，旧版保存的生图开关不会覆盖当前模型选择。

图片保存在 Codex 配置目录下的 `generated_images/hud/<会话ID>/`，重启后仍可查看。失败时会显示错误，HUD 不自动重试付费的生成请求。

当前支持通过 OpenAI 兼容的 `POST /images/generations` 接口生图。`gpt-image-*` 模型按官方契约默认返回 Base64，不发送不受支持的 `response_format` 参数；Gemini 兼容图片模型请求 `response_format=b64_json`。模型名称支持 `gpt-image-*`、`gemini-…image…` 和 `gemini-nano-banana*`；名称出现在模型列表中不代表供应商实际实现了这个接口。当前仅使用最新一条用户文字，参考图编辑、多轮改图和 WebSocket 生图尚未支持；附带参考图片时会明确报错，不会静默忽略。

这是 HUD 的兼容适配，不是 Codex 原生提供的生图开关。HUD 将 Images API 的结果保存为本地图片，再返回标准 Responses 消息和流式事件；不把图片模型当作编程代理执行工具或发送代理的系统指令。未把 Images API 计费伪装成普通文本 Token 消耗。

OpenAI 官方也提供 [Responses 的 image_generation 工具](https://developers.openai.com/api/docs/guides/tools-image-generation)：顶层保留对话模型，通过工具调用图片模型。该路径要求客户端和供应商共同支持，不能仅通过更换顶层模型名称启用。

修改源码后按需使用 HUD 的“管理 HUD → 重载 → HUD 与代理处理逻辑”，或手动按开发文档重载；编辑代码不会自动重启正在运行的 HUD。
