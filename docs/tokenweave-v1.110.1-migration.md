# LiteLLM v1.110.1 fork 迁移记录

候选分支基于 BerriAI 正式标签 `v1.110.1`（`47b394ba1b5bbe7f4b3f119a95b56957f4a32fad`）。迁移来源是 `tokenweave/1.91.x@60caa505fc`，以旧正式标签 `v1.91.4@4820053a81` 为界。旧主线较新标签有 89 个尚未在其历史中的提交，其中 35 个属于旧上游稳定版历史，另外 54 个位于 fork 第一父链，其中一次合入旧稳定标签的 merge 没有手工解冲突差异。以下清单覆盖其余 53 个第一父链提交。

三方试合逐一用原提交的父提交、上游新标签和原提交计算。53 个提交中只有 2 个直接适用。后续补丁在其前置补丁应用后可以转为可应用。发生冲突时保留上游语义并检查本项目所需行为。

候选尚未达到生产替换条件。核心阻塞项是 Codex 工具在 Bedrock Converse 的请求、SSE 和回传协议，Kimi Runtime Chat 的认证与推理，fallback 仅允许临时错误的策略，以及按部署的媒体计费。还需要验证镜像构建、运行和独立数据库副本上的迁移。表中标有待验证的项尚未达到行为等价。

## 候选验证

使用锁定的 Python 3.13 依赖，从候选源码成功构建上游 Rust 扩展。Seedance、Responses 工具和历史、模型列表、会话认证、MiniMax 视频及 Bedrock 转换共 1,612 个不同的单元测试通过。其中一个自签名 vLLM 模型列表用例在默认沙箱中因不能绑定本地端口失败，在允许本地端口后单独通过。Python 语法检查、变更行 Ruff 检查与 `git diff --check` 通过。

这些测试没有调用线上模型或数据库。现有 ECS 配置显式使用 `allowed_tool_types`、`drop_foreign_reasoning_items` 与 `fallback_on_transient_errors_only`，还需逐项确认新路由的实际生效条件。候选保留上游包版本 `1.110.1`，待迁移完成后再更新 fork 版本和锁文件。候选分支仅在本地，未推送或部署。

## 逐提交记录

| 原提交 | 功能 | 三方试合 | 候选处理 |
|---|---|---|---|
| `d086df4a4e` | fix(proxy): preserve team_models ordering in get_team_models | 冲突 | 上游的 get_team_models 用 dict.fromkeys 保序。上游现有回归覆盖配置顺序。 |
| `7cca2477d1` | ci: add GitHub Actions workflow to build linux/amd64 image for tokenweave branches | 可直接应用 | 已移植到 9924aec54a。需要验证新版 Dockerfile 的 amd64 构建和启动。 |
| `192b2c54e3` | fix(proxy): hide all-proxy-models sentinel & -fallback targets from listings | 冲突 | 已移植到 `10f4fed760`。将占位名和内部后备路由的过滤留在展示层。 |
| `f69aa15687` | test(proxy): update get_team_models test for all-proxy-models sentinel stripping | 冲突 | 旧 helper 的精确断言已过时。新版 helper 的上游行为需保留，覆盖客户端可见结果。 |
| `cf21459934` | internal test: enable features | 冲突 | 排除。本地试验提交在提供许可证配置时强制设置 premium_user，候选使用上游许可证校验。 |
| `1cc7745938` | fix(bedrock): suppress toolSpec strict only for Claude 5+ on Converse | 冲突 | 待对照 strict 工具被拒绝时的新版重试逻辑和 Claude 模型矩阵。 |
| `2bbb558cf5` | fix(proxy): add drop_namespace_tools litellm_param to strip Codex namespace tools | 冲突 | 待移植旧配置开关。和 d21951ee18、f9f9bab9b5、caa65acc16、60caa505fc 一起处理。 |
| `d21951ee18` | fix(proxy): generalize drop_namespace_tools to keep only function-type tools | 冲突 | 首次实现保留 function 的行为已被 1.91.x 后续提交扩展。应整体移植最终行为。 |
| `f9f9bab9b5` | feat(proxy): allowed_tool_types allowlist, applied on the native /responses path too | 冲突 | 新版没有同名的配置项。需要兼容旧白名单并验证原生 Responses、Chat 和桥接路径。 |
| `e4b0e678f4` | fix(responses): relocate Codex Desktop additional_tools input items into the tools param | 冲突 | 新版自带 hoist_additional_tools，在 Chat 桥接、Mantle、Fireworks 和 Codex passthrough 调用。其余原生路由需验证。 |
| `c99376c781` | fix(responses): drop empty additional_tools input items too | 冲突 | 新版 hoist_additional_tools 已移除空包装项。需随上一条验证实际启用条件。 |
| `e905975b37` | feat(mcp): report upstream serverInfo.version on single-server gateway endpoints | 冲突 | MCP 模块已从 _experimental 迁移。核对单服务器 initialize 的 serverInfo.version。 |
| `ca4abfe5da` | build(docker): pin wolfi python to 3.13 so image builds match requires-python | 冲突 | 新版使用 Wolfi Python 3.13。保留新版 Dockerfile，需后续实打镜像。 |
| `8ffca595c1` | build(docker): use uv managed CPython instead of the Wolfi apk python | 冲突 | 新版使用基镜像和包管理器提供的 Python 3.13，与旧 uv 下载路径不同。以实际镜像构建判断是否还需要补丁。 |
| `d7e8f3deb5` | feat(responses): reasoning_summary_override litellm_param for strict Bedrock models | 冲突 | 新版有按 provider 解析摘要的逻辑。待验证旧配置的强制摘要语义和三类请求情形。 |
| `709ea79515` | feat(responses): drop_foreign_reasoning_items litellm_param + affinity check routes normally on model-group switch | 冲突 | 新版 affinity 已有按部署剥离不可解密推理的路径。待跑跨模型及无 affinity 的切换回归。 |
| `590232b0bf` | fix(openai): route GPT-6 and Bedrock-named GPT-5.x through the GPT-5 reasoning config | 冲突 | 新版 OpenAI 模型配置已重构。待验证 GPT-6 和 Bedrock 模型名的配置选择。 |
| `62905ea0fd` | feat(responses): flatten_tool_schema_unions litellm_param for grok-4.7 on Bedrock | 冲突 | 同名开关未见于新版。待移植 Grok 的 schema union 兼容及回归。 |
| `bcabe1492a` | fix(images): Codex built-in image_gen compatibility (background passthrough + JSON /images/edits) | 冲突 | 待核对 Codex JSON 图片编辑入口和 background 字段。 |
| `005cba9174` | fix(cost): honor deployment per-image pricing for non-gpt-image models on the openai provider | 冲突 | 待核对 OpenAI 兼容图片模型的部署级单价。 |
| `ef6a1bf96a` | fix(responses): strip response-only logprobs from output_text parts in input history | 冲突 | 已移植到 `70463d54d8`，在同步 Responses 核心入口清理 replay 的 logprobs。 |
| `d52b5277fc` | feat(minimax): backport MiniMax-H3 / H3-Max video generation via /v1/videos (upstream #43705) | 冲突 | 上游含正式实现 71e508b65d（#43705）。需跑 H3/H3-Max 的协议和计费回归。 |
| `e6fca9f478` | feat(volcengine): backport Doubao-Seedance video generation via /v1/videos (upstream #46156) | 冲突 | 上游含正式实现 f81e657903（#46156）。额外的草稿链路单独移植。 |
| `61a5f8b360` | feat(router): model_info.hide_from_model_list hides a model group from /v1/models | 冲突 | 已移植到 `10f4fed760`。上游 discoverable:false 存在管理员例外。 |
| `d87c5efce0` | fix(cost): video calls bill at the deployment's own price, not the global cost map | 冲突 | 待验证视频部署价优先级以及轮询是否二次记费。 |
| `ba7b44b7be` | feat(cost): pixel-tier per-image pricing and free input images for deployment image pricing | 冲突 | 待适配像素分档图片价格及免费输入图。 |
| `c261f7f87b` | fix(images): keep provider-specific per-image fields (z_index, bounding_box, name) in ImageObject.provider_specific_fields | 冲突 | 待验证图层索引、名称和边界框能否保留到最终返回。 |
| `4019b10ad4` | feat(cost): bill layer-decomposition outputs per item at the layer rate | 冲突 | 待验证按输出图层数计费。 |
| `ef9b77f8f5` | feat(volcengine videos): Seedance 2.5 draft mode, 480p sample then a 1080p final render billed from the draft task | 可直接应用 | 已移植到 957ff16886。使用新版 Seedance 基础实现。 |
| `41e75c05ef` | fix(volcengine videos): accept litellm's encoded video id in draft_task.id and hand Ark the draft's own id | 冲突 | 在前置草稿提交后成功移植，候选提交 c6560bce19。 |
| `f3012996f9` | fix(gemini): detect Gemini 4 / nano-banana as Gemini 3+ (backport upstream b395bfefdd) | 冲突 | 新版已演进 Gemini 4 和 nano-banana 支持。待回归不同模型名的归类。 |
| `7da7af6507` | fix(gemini images): Vertex role, drop thought draft images, bill thoughtsTokenCount | 冲突 | 待回归 Vertex role、草稿图剔除和思考 token 计费。 |
| `8130e84bb4` | fix(gemini): map none/minimal to thinkingLevel low on gemini-3.7/3.8-flash | 冲突 | 待验证新版按模型映射 none/minimal。 |
| `faee459f18` | fix(gemini): map xhigh/max to thinkingLevel high; keep medium on Gemini 4 | 冲突 | 待验证新版按模型映射 xhigh/max，并保留支持 medium 的模型。 |
| `dbb95ad9bd` | feat(responses bridge): expose Codex custom tools as functions; drop empty assistant replays | 冲突 | 上游有 custom_tools.py 和 SSE 回转。待核对 Bedrock Runtime 与空 assistant 历史的覆盖。 |
| `7d193f254c` | feat(completion): opt-in inline_image_urls for upstreams that reject image URLs | 冲突 | 待核对旧 inline_image_urls 配置及安全下载边界。 |
| `2fd65f720d` | feat: per-deployment reasoning_effort_map to rewrite unsupported efforts | 冲突 | 待移植旧 per-deployment effort 映射或配置等价转换。 |
| `1786fa921a` | feat(gemini images): pass thinkingConfig through generations and edits | 冲突 | 待验证 Gemini image generations 和 edits 的 thinkingConfig。 |
| `d788af5105` | fix(image edits): keep output_format / output_compression / moderation | 冲突 | 待验证 edits 的 output_format、output_compression 和 moderation。 |
| `65c3b9976f` | feat(images): forward markup edits and preserve SVG and GIF formats | 冲突 | 待验证编辑时 markup 的传递，以及 SVG/GIF 格式维持。 |
| `696bc4a80a` | fix(build): pin verified Node runtime for Prisma generation | 冲突 | 新版有独立 UI Node 镜像和打包 Prisma 缓存。需验证 Prisma 版本、引擎可用性及离线启动。 |
| `15c69d9459` | fix(build): retain native C++ runtime and smoke test final image | 冲突 | 待验证目标容器的 C++ 运行时和最终镜像 import。以新版 Rust wheel 布局为基准。 |
| `8e1a733809` | fix(auth): isolate session tokens from stored secret ciphertext | 冲突 | 上游已有独立 bearer token 加密及同类回归。已在 `986d5d1f44` 补上无效 payload 拒绝处理，避免异常含令牌信息。 |
| `ff7541a4b2` | feat(images): support MiniMax H3 tokens and zero-retry deployments | 冲突 | 待验证 H3 图像 token 计量、部署级价格及零重试。 |
| `7a4047aca8` | fix(images): honor deployment token rates and edit input details | 冲突 | 待回归 edits 输入细目和每部署 token 单价。 |
| `c8ed273ffe` | feat(bedrock): add opt-in native chat with IAM auth for Kimi K3.1 | 冲突 | 上游已有 Bedrock Runtime Chat 显式路由和认证。待核对 Kimi profile、工具、推理、计费和真实 Task Role。 |
| `caa65acc16` | fix(tools): reconcile filtered tool choices and normalize empty tools | 冲突 | 待与白名单统一迁移。旧版本号、旧锁文件和旧历史验收结果不作为新版改动复用。 |
| `5aecdc24c9` | fix(bedrock): normalize Responses reasoning objects for Runtime chat | 冲突 | 待验证 Kimi reasoning 对象、summary-only、null、流式和工具请求。 |
| `42fae568bb` | fix(router): restrict recovery to transient errors when configured | 冲突 | 新版生产路由已重构到 Rust 路由栈，同名开关未见。需移植错误分类及流式/多跳恢复策略。 |
| `cfa57398a6` | fix(bedrock): forward GPT-6.2 Converse reasoning effort | 冲突 | 新版 Converse 已支持 OpenAI GPT 家族 reasoning.effort 的字符串映射。待验证 fork 的精确模型 ID、字典输入及开启工具时的 wire payload。 |
| `355a81837d` | feat(responses): bridge Codex tools to Bedrock Converse | 冲突 | 新版已有部分 namespace、custom 与 tool_search 支持。待验证 Converse 的 namespace、补丁、旧 Shell、工具搜索、结果回传及 SSE 双向语义。 |
| `384df3b130` | fix(responses): preserve tool search SDK compatibility | 冲突 | 待随 Converse 工具迁移核对 OpenAI SDK 对不同 tool_search input/output 形状的处理。 |
| `60caa505fc` | fix(tools): skip non-mapping entries when reconciling tool choice | 冲突 | 待与新版工具白名单和 tool_choice 校正一并移植，保留防御处理。 |
