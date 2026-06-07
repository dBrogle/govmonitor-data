"""Abstract base class for LLM services with structured output support."""

from abc import ABC, abstractmethod
from typing import TypeVar, Type

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMService(ABC):
    """Abstract LLM service that returns structured (Pydantic) output."""

    @abstractmethod
    def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
    ) -> T:
        """Send a prompt and parse the response into a Pydantic model.

        Args:
            system_prompt: System-level instructions for the model.
            user_prompt: The user message / main content.
            response_model: Pydantic model class defining the expected JSON schema.
            temperature: Sampling temperature (0 = deterministic).

        Returns:
            An instance of response_model parsed from the LLM's JSON response.
        """
        ...
