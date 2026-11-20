# LiteLLM v1.110.1 fork 迁移记录

候选分支基于 BerriAI 正式标签 `v1.110.1`（`47b394ba1b5bbe7f4b3f119a95b56957f4a32fad`）。迁移来源是 `tokenweave/1.91.x@60caa505fc`，以旧正式标签 `v1.91.4@4820053a81` 为界。旧主线较新标签有 89 个尚未在其历史中的提交，其中 35 个属于旧上游稳定版历史，另外 54 个位于 fork 第一父链，其中一次合入旧稳定标签的 merge 没有手工解冲突差异。下表逐项列出 54 个第一父链提交。

三方试合逐一用原提交的父提交、上游新标签和原提交计算。53 个提交中只有 2 个直接适用。后续补丁在其前置补丁应用后可以转为可应用。发生冲突时保留上游语义并检查本项目所需行为。

候选已在隔离 ECS 验证基本代理、Kimi Runtime Chat、GPT-6.2 Converse、Rust 路由以及合成数据升级和回读。生产切换前仍需核对生产数据库副本、供应商账单和下表标明的外部依赖。

## 候选验证

使用锁定的 Python 3.13 依赖，从候选源码成功构建上游 Rust 扩展。Seedance、Responses 工具和历史、模型列表、会话认证、MiniMax 视频及 Bedrock 转换共 1,612 个不同的单元测试通过。其中一个自签名 vLLM 模型列表用例在默认沙箱中因不能绑定本地端口失败，在允许本地端口后单独通过。Python 语法检查、变更行 Ruff 检查与 `git diff --check` 通过。

这些测试没有调用线上模型或数据库。现有 ECS 配置显式使用 `allowed_tool_types`、`drop_foreign_reasoning_items` 与 `fallback_on_transient_errors_only`，还需逐项确认新路由的实际生效条件。首个隔离测试构建使用 `1.110.1+tokenweave.20261120.1`。该版本用于建立镜像及数据库启动基线，功能迁移进度见下表。

## 工具过滤迁移

`1.110.1+tokenweave.20261120.2` 恢复部署级 `allowed_tool_types`、旧版 `drop_namespace_tools` 以及过滤后的具名工具选择校正。Responses 路径先用新版上游的 hoist 逻辑展开 `additional_tools`。启用 `bridge_custom_tools_as_functions` 的 Responses 转 Chat 路由会保留允许转换的 custom 工具，由上游的转换器处理。

验证覆盖原生 Responses、Responses 转 Chat、Rust 路由转发部署配置、Bedrock Chat 转换和空工具列表。GPT-6.2 的额外工具类型、真实 SSE 和多轮回传仍需在隔离环境验收。

## Kimi Runtime 与回退规则迁移

`1.110.1+tokenweave.20261120.3` 为美国版 Kimi K3.1 推理配置声明 Runtime Chat 支持工具与推理同时使用。Responses 转 Chat 复用新版上游的推理对象拆分和 custom 工具转换。签名前测试覆盖 effort 和 summary 并存、只传 summary、reasoning 为空三种请求。

同版恢复 `fallback_on_transient_errors_only`。Rust 路由在重试、逐跳回退和流式恢复前判断失败。跨模型回退链遇到第一个永久错误即返回该错误。Rust 核心 332 项测试通过，Python 至 Rust 路由的 400、429、503 验证通过。

## Custom 工具流与 Seedream 计价

`1.110.1+tokenweave.20261120.4` 对 Responses 转 Chat 的 custom 工具发出 `response.custom_tool_call_input.delta` 和 `response.custom_tool_call_input.done`。增量携带去掉 Chat 工具 JSON 包装后的输入。549 项转换和流式测试通过。

同版恢复 Seedream 的部署级像素分档、图层逐张计费和输入图免费额度。379 项计费及响应类型测试通过。真实 SSE 客户端和媒体账单仍需在隔离环境复验。

## 部署级兼容开关

`1.110.1+tokenweave.20261120.5` 恢复 `reasoning_effort_map`、`reasoning_summary_override`、`inline_image_urls` 和 `flatten_tool_schema_unions`。沿用新版图片下载保护，工具 schema 转换支持引用、命名空间和递归限制。原生及桥接 Responses 的推理和工具 schema 用例以及图片 URL 用例通过。Grok 和图片上游仍需实测。

## 图片协议和计费复验

`1.110.1+tokenweave.20261120.6` 恢复 Codex JSON 图片编辑、multipart 标注文件及输出选项。内联图片只接收支持的 base64 图片类型，禁止代理获取外部 URL。旧分支的图片编辑、Codex 图片工具及 MiniMax H3 模拟上游用例共 25 项通过，覆盖生成和编辑的计量、部分失败结果、按部署的费率和零重试。

Gemini 图片生成和编辑都发送 Vertex 所需的 user role，转发客户端 thinkingConfig，过滤带 thought 标记的草稿图，并将单独报告的思考 token 按输出文本 token 计量。这一轮使用合成请求、响应及账单费率。

## 原生客户端工具与 MCP 版本

`1.110.1+tokenweave.20261120.7` 恢复部署级 `bridge_responses_tools_as_functions`。Responses 转 Chat 将 apply_patch 和 local_shell 映射为函数，重组分片后发出原生工具项目，并把客户端结果按 call ID 回传。直接代理的 OpenAI SDK 流式测试通过，使用合成上游。命名空间与客户端 tool_search 沿用新版转换，混合 MCP 调用批次保留给客户端。

单服务器 MCP 路径报告上游 initialize 的 serverInfo.version，聚合路径报告网关版本。按 SDK 2 的 InitializeResult 读取 server_info，使用既有的上游会话缓存。新版 Discover 协议没有该字段时沿用网关版本。

## 模型兼容和 Converse 流式补齐

`1.110.1+tokenweave.20261120.8` 修补 Bedrock Converse 同时返回文本和原生工具时的 Responses 终态序列化。恢复 Claude 5+ 工具顶层 strict 限制、Gemini 指定部署的 xhigh/max 映射、Bedrock 推理配置名归类，以及直连 Responses 的部署级加密推理归属过滤。测试覆盖历史同源、外来源和未标记三种加密内容，跨组 affinity 仍由上游路由处理。

## 逐提交记录

| 原提交 | 功能 | 三方试合 | 候选处理 |
|---|---|---|---|
| `f30735b3b3` | Merge upstream v1.91.4 into tokenweave/1.91.x | 无独立冲突改动 | `git show --remerge-diff` 无输出差异。较新的上游基线已包含旧版历史。 |
| `d086df4a4e` | fix(proxy): preserve team_models ordering in get_team_models | 冲突 | 上游的 get_team_models 用 dict.fromkeys 保序。上游现有回归覆盖配置顺序。 |
| `7cca2477d1` | ci: add GitHub Actions workflow to build linux/amd64 image for tokenweave branches | 可直接应用 | 已移植到 9924aec54a。.5 的完整 amd64 CI 已通过，候选系列已在隔离 ECS 启动。 |
| `192b2c54e3` | fix(proxy): hide all-proxy-models sentinel & -fallback targets from listings | 冲突 | 已移植到 `10f4fed760`。将占位名和内部后备路由的过滤留在展示层。 |
| `f69aa15687` | test(proxy): update get_team_models test for all-proxy-models sentinel stripping | 冲突 | 保留新版对团队通配符的展开语义，模型列表过滤回归通过。 |
| `cf21459934` | internal test: enable features | 冲突 | 排除。本地试验提交在提供许可证配置时强制设置 premium_user，候选使用上游许可证校验。 |
| `1cc7745938` | fix(bedrock): suppress toolSpec strict only for Claude 5+ on Converse | 冲突 | Claude 5+ 的工具顶层 strict 已抑制，保留受支持的 JSON schema 约束，Converse 模型矩阵回归通过。 |
| `2bbb558cf5` | fix(proxy): add drop_namespace_tools litellm_param to strip Codex namespace tools | 冲突 | 已恢复旧配置开关并与允许列表共用过滤逻辑。 |
| `d21951ee18` | fix(proxy): generalize drop_namespace_tools to keep only function-type tools | 冲突 | 首次实现保留 function 的行为已被 1.91.x 后续提交扩展。应整体移植最终行为。 |
| `f9f9bab9b5` | feat(proxy): allowed_tool_types allowlist, applied on the native /responses path too | 冲突 | 已恢复部署级允许列表，原生 Responses、Chat、桥接和具名工具选择回归通过。 |
| `e4b0e678f4` | fix(responses): relocate Codex Desktop additional_tools input items into the tools param | 冲突 | 保留上游 hoist，并在原生 Responses 白名单入口复用，覆盖旧配置的有效范围。 |
| `c99376c781` | fix(responses): drop empty additional_tools input items too | 冲突 | 上游 hoist 删除空包装项，原生白名单及桥接回归通过。 |
| `e905975b37` | feat(mcp): report upstream serverInfo.version on single-server gateway endpoints | 冲突 | 已恢复单服务器 initialize 的上游版本，覆盖缓存和聚合范围。 |
| `ca4abfe5da` | build(docker): pin wolfi python to 3.13 so image builds match requires-python | 冲突 | 新版 Wolfi Python 3.13 已完成 amd64 构建及启动。 |
| `8ffca595c1` | build(docker): use uv managed CPython instead of the Wolfi apk python | 冲突 | 沿用新版 Python 3.13 安装路径，成功构建、导入及运行。 |
| `d7e8f3deb5` | feat(responses): reasoning_summary_override litellm_param for strict Bedrock models | 冲突 | 旧配置摘要覆盖已迁移。原生 Responses 和转 Chat 都覆盖替换、删除及未请求摘要三类行为。 |
| `709ea79515` | feat(responses): drop_foreign_reasoning_items litellm_param + affinity check routes normally on model-group switch | 冲突 | 跨组 affinity 沿用上游逻辑。恢复了部署级直连过滤，覆盖同源、跨源和未标记加密项。 |
| `590232b0bf` | fix(openai): route GPT-6 and Bedrock-named GPT-5.x through the GPT-5 reasoning config | 冲突 | 已补齐带 Bedrock 前缀的 GPT-5.x/6 名称归类，工具加显式推理的 Responses 路由通过。 |
| `62905ea0fd` | feat(responses): flatten_tool_schema_unions litellm_param for grok-4.7 on Bedrock | 冲突 | 已恢复部署级开关，覆盖命名空间、hoisted tools、引用和有界递归。Grok 上游待实测。 |
| `bcabe1492a` | fix(images): Codex built-in image_gen compatibility (background passthrough + JSON /images/edits) | 冲突 | 已恢复 JSON 编辑入口，覆盖 background、HTTP multipart 转发及拒绝外部 URL。 |
| `005cba9174` | fix(cost): honor deployment per-image pricing for non-gpt-image models on the openai provider | 冲突 | 已覆盖 OpenAI 兼容图片部署的逐图价格及 Seedream 图层费率。 |
| `ef6a1bf96a` | fix(responses): strip response-only logprobs from output_text parts in input history | 冲突 | 已移植到 `70463d54d8`，在同步 Responses 核心入口清理 replay 的 logprobs。 |
| `d52b5277fc` | feat(minimax): backport MiniMax-H3 / H3-Max video generation via /v1/videos (upstream #43705) | 冲突 | 采用上游正式实现，H3/H3-Max 协议相关回归通过。 |
| `e6fca9f478` | feat(volcengine): backport Doubao-Seedance video generation via /v1/videos (upstream #46156) | 冲突 | 上游含正式实现 f81e657903（#46156）。额外的草稿链路单独移植。 |
| `61a5f8b360` | feat(router): model_info.hide_from_model_list hides a model group from /v1/models | 冲突 | 已移植到 `10f4fed760`。上游 discoverable:false 存在管理员例外。 |
| `d87c5efce0` | fix(cost): video calls bill at the deployment's own price, not the global cost map | 冲突 | 上游已有部署价优先级和 MiniMax 轮询计费修复。MiniMax 与 Volcengine 共 333 项单测通过，实际账单待验。 |
| `ba7b44b7be` | feat(cost): pixel-tier per-image pricing and free input images for deployment image pricing | 冲突 | 已迁移像素分档和免费输入图，覆盖响应尺寸、请求尺寸、usage 和默认价格。 |
| `c261f7f87b` | fix(images): keep provider-specific per-image fields (z_index, bounding_box, name) in ImageObject.provider_specific_fields | 冲突 | 补齐 ImageObject 对原始图层元数据的收集，复用新版序列化逻辑。返回结构回归通过。 |
| `4019b10ad4` | feat(cost): bill layer-decomposition outputs per item at the layer rate | 冲突 | 已迁移每个图层以各自尺寸定价，单测通过。 |
| `ef9b77f8f5` | feat(volcengine videos): Seedance 2.5 draft mode, 480p sample then a 1080p final render billed from the draft task | 可直接应用 | 已移植到 957ff16886。使用新版 Seedance 基础实现。 |
| `41e75c05ef` | fix(volcengine videos): accept litellm's encoded video id in draft_task.id and hand Ark the draft's own id | 冲突 | 在前置草稿提交后成功移植，候选提交 c6560bce19。 |
| `f3012996f9` | fix(gemini): detect Gemini 4 / nano-banana as Gemini 3+ (backport upstream b395bfefdd) | 冲突 | 本地模型配置探针确认 Gemini 4 Argon 与 nano-banana 2.1 都归入 Gemini 3+。 |
| `7da7af6507` | fix(gemini images): Vertex role, drop thought draft images, bill thoughtsTokenCount | 冲突 | 已恢复 Vertex user role、草稿图剔除及思考 token 计量，合成响应回归通过。 |
| `8130e84bb4` | fix(gemini): map none/minimal to thinkingLevel low on gemini-3.7/3.8-flash | 冲突 | 现有 Gemini 3.7/3.8 模型矩阵覆盖 none/minimal 到低档。 |
| `faee459f18` | fix(gemini): map xhigh/max to thinkingLevel high; keep medium on Gemini 4 | 冲突 | 相关旧部署的 xhigh/max 恢复为 high，Gemini 4 Argon 的 medium 和声明支持 xhigh 的新模型保持各自档位。 |
| `dbb95ad9bd` | feat(responses bridge): expose Codex custom tools as functions; drop empty assistant replays | 冲突 | 已复验 Bedrock Runtime 的 custom SSE、推理和结果回传，空 assistant 历史沿用新版转换。 |
| `7d193f254c` | feat(completion): opt-in inline_image_urls for upstreams that reject image URLs | 冲突 | 已恢复旧配置开关，复用新版带 SSRF 检查和大小限制的下载器。端到端构造的 Chat 请求只替换远程图像链接。 |
| `2fd65f720d` | feat: per-deployment reasoning_effort_map to rewrite unsupported efforts | 冲突 | 已恢复旧映射，Responses 转 Chat 路径只映射一次。 |
| `1786fa921a` | feat(gemini images): pass thinkingConfig through generations and edits | 冲突 | 已恢复生成和编辑的 thinkingConfig，支持 JSON 对象及 multipart 中的 JSON 字符串。 |
| `d788af5105` | fix(image edits): keep output_format / output_compression / moderation | 冲突 | 已恢复 output_format、output_compression 和 moderation，multipart 请求回归通过。 |
| `65c3b9976f` | feat(images): forward markup edits and preserve SVG and GIF formats | 冲突 | 已恢复 markup 文件，并保留 SVG/GIF 输出格式和文件 MIME。验证了 GIF 标注及拒绝与 mask 并用。 |
| `696bc4a80a` | fix(build): pin verified Node runtime for Prisma generation | 冲突 | 新版 Prisma 已在无外网的 Docker 测试网络完成迁移，并在隔离 ECS 读取迁移记录。 |
| `15c69d9459` | fix(build): retain native C++ runtime and smoke test final image | 冲突 | 新版镜像成功导入 LiteLLM 与 Rust 扩展，隔离 ECS 的实际代理和回退烟测通过。 |
| `8e1a733809` | fix(auth): isolate session tokens from stored secret ciphertext | 冲突 | 上游已有独立 bearer token 加密及同类回归。已在 `986d5d1f44` 补上无效 payload 拒绝处理，避免异常含令牌信息。 |
| `ff7541a4b2` | feat(images): support MiniMax H3 tokens and zero-retry deployments | 冲突 | MiniMax H3 和 H3 Highspeed 的生成、编辑分项计量及零重试回归通过，供应商实际账单待验。 |
| `7a4047aca8` | fix(images): honor deployment token rates and edit input details | 冲突 | 生成和编辑分别通过部署级文本/图片 token 费率回归，供应商实际账单待验。 |
| `c8ed273ffe` | feat(bedrock): add opt-in native chat with IAM auth for Kimi K3.1 | 冲突 | Kimi 路由确认使用原生 Runtime Chat，隔离任务角色授权的工具、推理和回传通过。实际账单需对账。 |
| `caa65acc16` | fix(tools): reconcile filtered tool choices and normalize empty tools | 冲突 | 具名工具选择随过滤后的工具集校正，空工具集回归通过。 |
| `5aecdc24c9` | fix(bedrock): normalize Responses reasoning objects for Runtime chat | 冲突 | 签名前覆盖 effort/summary、summary-only 和空值。隔离 ECS 的真实推理及 custom 流回传通过。 |
| `42fae568bb` | fix(router): restrict recovery to transient errors when configured | 冲突 | 已移植至 Rust 路由栈，覆盖错误分类、流式恢复与多跳回退。 |
| `cfa57398a6` | fix(bedrock): forward GPT-6.2 Converse reasoning effort | 冲突 | 精确 GPT-6.2 Sol 推理配置 ARN 的请求映射通过，真实 Converse 工具烟测覆盖推理参数。 |
| `355a81837d` | feat(responses): bridge Codex tools to Bedrock Converse | 冲突 | 已迁移 apply_patch 和 local_shell。模拟上游的 OpenAI SDK 流式和回传通过。真实 Converse 复验待完成。 |
| `384df3b130` | fix(responses): preserve tool search SDK compatibility | 冲突 | 隔离 Converse 的真实工具搜索通过 OpenAI SDK 流式解析与结果回传。 |
| `60caa505fc` | fix(tools): skip non-mapping entries when reconciling tool choice | 冲突 | 与白名单统一迁移，校正具名工具时跳过非对象条目。 |
