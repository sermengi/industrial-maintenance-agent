from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from maintenance_agent.core.config import get_settings

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 1024

MessageRole = Literal["user", "assistant"]
ToolChoiceType = Literal["auto", "any", "tool", "none"]
LLMContentBlock = str | Sequence[Mapping[str, Any]]


class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: LLMContentBlock


class LLMTool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class LLMToolChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ToolChoiceType
    name: str | None = None


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)


class LLMClient(Protocol):
    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        pass


class AnthropicLLMClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client: Any | None = None

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                raise RuntimeError(
                    "The anthropic package is required for the Anthropic LLM client."
                ) from exc

            self._client = cast(Any, AsyncAnthropic(api_key=self.api_key))

        client = cast(Any, self._client)
        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[message.model_dump() for message in messages],
            tools=[tool.model_dump() for tool in tools] if tools is not None else None,
            tool_choice=tool_choice.model_dump(exclude_none=True)
            if tool_choice is not None
            else None,
        )

        return _normalize_anthropic_response(response.content)


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for the Anthropic LLM client.")
    return AnthropicLLMClient(
        settings.anthropic_api_key,
        model=settings.anthropic_model,
    )


def _normalize_anthropic_response(content_blocks: Sequence[Any]) -> LLMResponse:
    text_blocks: list[str] = []
    tool_calls: list[ToolCallRequest] = []

    for block in content_blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = cast(str | None, getattr(block, "text", None))
            if text:
                text_blocks.append(text)
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCallRequest(
                    id=cast(str, block.id),
                    name=cast(str, block.name),
                    input=dict(cast(Mapping[str, Any], getattr(block, "input", {}))),
                )
            )

    text = "\n".join(text_blocks).strip() or None
    return LLMResponse(text=text, tool_calls=tool_calls)
