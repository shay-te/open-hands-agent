# OpenHands — kato

Provider-specific configuration for the OpenHands agent backend (LLM providers, container settings).

## Setting Up OpenHands With Bedrock

Use this when `OPENHANDS_LLM_MODEL` starts with `bedrock/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=bedrock/your-model-id
OPENHANDS_LLM_API_KEY=
OPENHANDS_LLM_BASE_URL=
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-west-2
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

For Bedrock auth, choose one path:

- Standard AWS credentials: set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION_NAME`. Set `AWS_SESSION_TOKEN` too when the credentials are temporary. Leave `AWS_BEARER_TOKEN_BEDROCK` empty.
- Bedrock bearer token: set `AWS_BEARER_TOKEN_BEDROCK`. Leave `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`, and `AWS_SESSION_TOKEN` empty.

## Setting Up OpenHands With OpenRouter

Use this when `OPENHANDS_LLM_MODEL` starts with `openrouter/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=openrouter/openai/gpt-4o-mini
OPENHANDS_LLM_API_KEY=...
OPENHANDS_LLM_BASE_URL=https://openrouter.ai/api/v1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION_NAME=
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

OpenRouter requires both `OPENHANDS_LLM_API_KEY` and `OPENHANDS_LLM_BASE_URL`. Leave the AWS Bedrock variables empty for OpenRouter runs.
