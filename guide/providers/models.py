import json

import httpx
from pydantic import BaseModel, ValidationError

from guide.providers.base import ProviderError
from guide.settings import ROOT, Settings

MODELS = json.loads((ROOT / "guide/data/models.json").read_text())


class HttpModel:
    def __init__(self, provider: str, settings: Settings, client: httpx.AsyncClient, store):
        self.provider, self.settings, self.client, self.store = provider, settings, client, store
        self.model = MODELS[provider]["research"]

    async def generate(self, instruction: str, data: dict, schema: type[BaseModel]) -> BaseModel:
        key = self.settings.key(self.provider)
        if not key:
            raise ProviderError("Provider credentials are unavailable")
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded) > 35000:
            raise ProviderError("Research context exceeded its limit")
        reservation = await self.store.reserve(
            self.settings.budget_id,
            self.settings.provider_call_reserve_usd,
            self.settings.budget_usd,
            self.provider,
        )
        specification = schema.model_json_schema()
        if schema.__name__ == "Draft":
            quotes = list(
                dict.fromkeys(
                    quote
                    for source in data.get("sources", [])
                    for quote in source.get("quotes", [])
                )
            )
            if not quotes:
                raise ProviderError("No exact evidence passages are available")
            specification["$defs"]["Finding"]["properties"]["quote"]["enum"] = quotes
        instruction += "\nReturn only JSON matching this schema: " + json.dumps(specification)
        try:
            if self.provider == "openai":
                response = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": instruction},
                            {"role": "user", "content": encoded},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": schema.__name__,
                                "strict": True,
                                "schema": specification,
                            },
                        },
                        "max_tokens": 1000,
                        "temperature": 0,
                        "store": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
            elif self.provider == "gemini":
                response = await self.client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={
                        "systemInstruction": {"parts": [{"text": instruction}]},
                        "contents": [{"role": "user", "parts": [{"text": encoded}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "maxOutputTokens": 1000,
                            "temperature": 0,
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = "".join(
                    part.get("text", "") for part in payload["candidates"][0]["content"]["parts"]
                )
            elif self.provider == "anthropic":
                response = await self.client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": self.model,
                        "system": instruction,
                        "messages": [{"role": "user", "content": encoded}],
                        "max_tokens": 1000,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = "".join(part.get("text", "") for part in payload["content"])
            else:
                raise ProviderError("Unsupported model provider")
            usage = payload.get("usage", payload.get("usageMetadata", {}))
            await self.store.usage(reservation, {"model": self.model, "tokens": usage})
            return schema.model_validate_json(content)
        except (
            httpx.HTTPError,
            ValidationError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as error:
            raise ProviderError("Provider request failed or returned invalid data") from error
