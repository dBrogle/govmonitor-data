"""OpenRouter LLM service implementation with JSON schema structured output."""

import json
from typing import Type, TypeVar

import requests
from pydantic import BaseModel

from .base import LLMService

T = TypeVar("T", bound=BaseModel)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterService(LLMService):
    """LLM service backed by OpenRouter's chat completions API.

    Uses response_format with json_schema to enforce structured output.
    """

    def __init__(self, api_key: str, model: str = "x-ai/grok-4.3", temperature: float = 0.0):
        self.api_key = api_key
        self.model = model
        # Temperature 0 by default, owned here as the single source of truth so every scored
        # artifact is as reproducible as the model allows. Stamped into each analysis output.
        self.temperature = temperature

    def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float | None = None,
    ) -> T:
        # Fall back to the service-level temperature (0.0) unless a call explicitly overrides it.
        temperature = self.temperature if temperature is None else temperature
        json_schema = response_model.model_json_schema()

        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        r = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        r.raise_for_status()

        data = r.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return response_model.model_validate(parsed)
