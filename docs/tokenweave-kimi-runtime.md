# Bedrock Runtime native Chat Completions

## 1.91.4+tokenweave.20261116

Adds the explicit `bedrock/chat_completions/` route for Kimi K3.1 on Bedrock Runtime. Existing routes and model defaults are unchanged. This route uses the AWS credential chain and SigV4, including automatically refreshed ECS Task Role credentials. It ignores bearer API keys and removes forwarded Authorization headers before signing

Example configuration:

```yaml
model_list:
  - model_name: bedrock-kimi-k3.1
    litellm_params:
      model: bedrock/chat_completions/us.moonshotai.kimi-k3.1
      aws_region_name: us-east-1
      use_chat_completions_api: true
      max_tokens: 16384
      timeout: 900
```

The region selects the Runtime endpoint; the `us.` inference profile selects the US routing geography. The base `moonshotai.kimi-k3.1` ID cannot be invoked directly. No `api_key`, static AWS keys, or self-assume-role configuration is needed in ECS

Requests use `/openai/v1/chat/completions` for both streaming and non-streaming calls. The adapter retains tool results, prior reasoning content, images and cache controls. Responses requests use the existing chat-completions bridge; this change does not add native Bedrock Responses support

The built-in US route price entry uses Standard tier USD rates per million tokens: input 4.40, output 22.00, cache read 0.22, cache write 5.50. Runtime's `cache_write_tokens` is normalized to `cache_creation_tokens` for the existing billing engine, in both response modes. The original field remains available, and the provider response is not mutated. The sample deployment uses Standard tier; other service tiers and global pricing are outside this validation

References: [AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-moonshot-ai-kimi-k3-1-highspeed.html) and [upstream Task Role migration](https://github.com/zhangzhenhuajack/aws-builder/commit/391fb860af1b9d40676a4529f62f292a3ffc24d2)

## Validation on 2026-11-16

An isolated proxy on `127.0.0.1:4186` called real AWS Bedrock through local AWS SDK credentials and SigV4, without a Bedrock API key. This validates the protocol and model, not an ECS deployment or the production Task Role

```sh
curl --silent --show-error http://127.0.0.1:4186/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data '{"model":"bedrock-kimi-k3.1","messages":[{"role":"user","content":"Reply with exactly OK."}],"max_tokens":32,"reasoning_effort":"none"}'
```

Observed HTTP 200 with assistant content `OK`. Streaming returned text, final usage and `[DONE]`. A forced `lookup` function call with low reasoning succeeded; resubmitting the actual assistant reasoning and a tool result of `42` succeeded and returned `42`. `/v1/responses` with a 32-token cap returned status `completed` and text `OK`

The final targeted suite passed 69 tests; Ruff, adapter type checking and offline lock validation passed. A final real request reported 23 input and 10 output tokens, with `x-litellm-response-cost: 0.0003212`, exactly matching the configured USD rates

Offline transport tests verify SigV4 against the actual serialized body and temporary session token, sync/async SSE, tools, history, cache preservation, API-key isolation and price calculation. A fixture with 1000 input tokens (400 cached reads and 100 writes) plus 20 output tokens costs USD 0.003278

Production rollout remains separate: build the new fork commit, deploy that image, then add the model configuration. Uploading this configuration to an older image is insufficient. Verify the deployed Task Role, stream/tool behavior and spend records after rollout. No fallback or existing model needs to be changed

## 1.91.4+tokenweave.20261116.1

Restores the tool filtering fixes left on `feat/allowed-tool-types` in July: normalize an initially empty tools list to `None` and reconcile a named `tool_choice` after filtering. Both chat completions and native Responses are covered, including the function-only legacy filter. The existing additional-tools relocation remains intact. This release also includes the Kimi Runtime IAM adapter above

## 1.91.4+tokenweave.20261116.2

Fix Runtime Chat requests originating from Responses clients that include `reasoning.summary`. The bridge preserves the reasoning object; this adapter now extracts its effort string before sync/async request serialization and SigV4 signing. Summary-only or null-effort objects omit the field and leave the model default intact. Scalar efforts and other providers are unchanged

Regression coverage includes all seven effort levels, auto/detailed/concise/null summaries, summary-only input, caller immutability, and signed Responses streaming/non-streaming transport. The previous release tests covered effort-only input and missed this client shape. A similar bridge type mismatch is documented in [LiteLLM issue 37452](https://github.com/BerriAI/litellm/issues/37452); no upstream code was merged
