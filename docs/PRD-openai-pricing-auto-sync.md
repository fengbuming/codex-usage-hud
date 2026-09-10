# PRD：OpenAI 官方模型价格自动同步

## 1. 结论与范围

当前代码已经具备“供应商模型价格表、价格来源 URL、手动拉取并预览、差异校验、定时任务可复用的运行时配置、价格版本/审计记录”等基础，但尚未具备 OpenAI 官方 HTML 解析器、定时调度、更新红点和差异弹窗。因此方案可在现有 Renderer/CDP 设置链路上实现，不能直接把旧 HTTP `/prices/fetch` 当作完成方案：该接口目前明确返回 409，要求 Renderer 先预览。

官方首选来源为 [OpenAI Models](https://developers.openai.com/api/docs/models)，价格字段随模型条目展示；解析器必须保留来源 URL、抓取时间、页面版本/哈希，并允许页面结构变化时安全失败。必要时以 [OpenAI API Pricing](https://platform.openai.com/docs/pricing) 作为补充校验来源。

## 2. 用户故事

用户打开设置即可看到 OpenAI 默认价格来源；系统按配置周期后台检查。发现新增、删除或 input/cached-input/output/reasoning 单价变化时，设置入口显示小红点，点击后弹窗展示本地值、官方值、差异和来源。用户可手动“拉取并预览”，确认生效时间后写入价格版本；失败不改变本地价格。

## 3. 现状证据

- `config.ProviderSettings` 已有 `model_prices`、`pricing_url`；`UserConfig` 已有全局同类字段及 `pricing_versions`、`pricing_audit`。
- Renderer 设置页已有“计费单价获取地址”和“拉取并预览”（`renderer_assets/settings_shell.py`），并已有价格导入预览/生效时间确认流程。
- `settings_bridge.py` 的旧 `/prices/fetch` 被停用并返回 409；新能力应复用 Renderer 的 `fetchPricesPreview`/导入提交路径。
- 现有价格模型支持 input、cached_input、cache_write、output、reasoning 等字段，可扩展 metadata 而无需立即改表结构。

## 4. 目标与非目标

目标：OpenAI 默认 URL；可配置周期（关闭/每日/每周/自定义小时）；手动触发；HTML 解析、标准化、差异分类；红点、弹窗、预览确认；失败重试与审计。

非目标：绕过登录抓取需认证页面；自动无确认覆盖用户价格；把网页当稳定官方 JSON API；新增 Qt/Tk 行为。

## 5. 数据与接口

新增配置：`pricing_sync.enabled`、`interval_hours`、`last_checked_at`、`last_success_at`、`last_result`、`unread_change_count`、`source_url`（默认 models URL）。标准记录：`provider, model_id, display_name, input, cached_input, output, reasoning, currency, unit, source_url, checked_at, source_hash`。

新增内部命令：`fetchPricesPreview(provider, reason)`、`applyPricingPreview(preview_id, effective_at)`、`dismissPricingChanges(provider)`、`getPricingSyncStatus()`。后台任务只产生 preview/status 事件，不直接写价格；应用确认后调用既有版本化保存。

## 6. 解析与对比规则

请求限制 2 MiB、超时 15 秒、仅 HTTPS（允许用户配置的受信 URL）；解析 HTML 表格/语义节点，金额统一为 USD/1M tokens，支持 `$0.20`、`0.20 USD` 等格式。模型 ID 以页面明确 ID 为准，禁止仅凭展示名猜测。对比键为 `(provider, model_id, price_type, unit, currency)`，金额使用 Decimal；分类为新增、删除、上涨、下降、字段补齐、无法匹配。无法可靠解析或页面哈希变化但零条记录时判失败并保留旧值。

## 7. UI/交互

设置页供应商价格区域标题旁增加红点（无障碍文本“有 N 项价格更新”）。点击打开差异弹窗，按模型分组显示本地/官方/变化百分比/来源/抓取时间；提供“查看来源”“进入预览”“忽略本次”。手动按钮显示进行中、成功、失败状态；输入 URL 或失焦不触发请求，只有按钮点击触发。

## 8. 调度与可靠性

Renderer 启动后注册单一后台调度器；无到期任务不轮询。到期执行一次，失败采用 1h、4h、24h 退避并记录错误；网络不可用不弹窗骚扰。应用退出前取消任务。跨平台使用 Python 标准库/现有 HTTP 客户端，解析器独立于 UI，便于单测。

## 9. 安全与合规

禁止执行页面脚本或提交凭据；遵守 robots/服务条款和合理频率；记录 URL、状态码、哈希和错误摘要，不记录 Authorization/Cookie。官方页面结构变化触发可诊断失败。

## 10. 验收标准

1. 新安装默认 OpenAI Models URL，旧配置可迁移且不改价格。
2. 使用固定 HTML fixture 能正确解析五类价格并生成新增/变更/删除差异。
3. 手动拉取只生成预览；确认生效后产生新 `pricing_version` 和审计记录，取消不落库。
4. 定时到期自动执行一次；成功/失败状态可在设置页查看，失败不覆盖旧值。
5. 有未读差异时红点出现，查看或忽略后消失；重启后未读状态保持。
6. 解析失败、超时、超大响应、非 HTTPS 均有可读错误且价格不变。

## 11. 实施拆分

Phase 1：抽取 `OpenAIModelsParser`、标准 schema、fixture 单测。Phase 2：接入现有 preview/apply 价格版本链路和状态持久化。Phase 3：Renderer 红点/差异弹窗/CDP 命令。Phase 4：后台调度、退避、重启恢复、文档与迁移。每阶段仅运行受影响的 focused tests。

## 12. 已实施的区域网络方案

OpenAI 未提供可作为权威来源的中国大陆价格镜像。项目使用 GitHub Actions 每 4 小时抓取 OpenAI Models 与 Pricing 官方页面：Models 页校验默认输入/输出价格，Pricing 页补齐 cached-input 与 cache-write；同名模型价格不一致时停止生成，不发布新快照。

HUD 依次读取项目 `pricing-snapshot` 数据分支的 GitHub raw 快照、现有 GitHub 区域传输线路和安装包内最近一次快照。数据分支由 GitHub Actions 独占更新，避免绕过 `main` 的 PR 保护。区域线路仅传输项目生成的结构化快照，不作为 OpenAI 官方来源；快照保留两份官方 URL、抓取时间和页面 SHA-256。所有远端线路失败时继续使用安装包内快照并保留当前价格，用户不需要配置本机代理。
