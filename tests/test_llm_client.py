import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from maintenance_agent.core.config import Settings, get_settings
from maintenance_agent.llm.client import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicLLMClient,
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolChoice,
    ToolCallRequest,
    _normalize_anthropic_response,
    get_llm_client,
)


def test_llm_response_is_project_owned_shape() -> None:
    response = LLMResponse(
        text="Use the maintenance history.",
        tool_calls=[
            ToolCallRequest(
                id="toolu_1",
                name="get_maintenance_history",
                input={},
            )
        ],
    )

    assert response.text == "Use the maintenance history."
    assert response.tool_calls[0].name == "get_maintenance_history"


def test_normalizes_text_and_tool_use_blocks_without_raw_response_shape() -> None:
    response = _normalize_anthropic_response(
        [
            _TextBlock(text="Checking status."),
            _ToolUseBlock(
                id="toolu_1",
                name="get_asset_status",
                input={"asset_id": "PUMP-103"},
            ),
            _TextBlock(text="Then reviewing history."),
        ]
    )

    assert response == LLMResponse(
        text="Checking status.\nThen reviewing history.",
        tool_calls=[
            ToolCallRequest(
                id="toolu_1",
                name="get_asset_status",
                input={"asset_id": "PUMP-103"},
            )
        ],
    )


def test_empty_tool_calls_is_stop_signal() -> None:
    response = _normalize_anthropic_response([_TextBlock(text="No more evidence needed.")])

    assert response.text == "No more evidence needed."
    assert response.tool_calls == []


def test_get_llm_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            get_llm_client()
    finally:
        get_settings.cache_clear()


def test_settings_include_anthropic_configuration() -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", ANTHROPIC_MODEL="custom-model")

    assert settings.anthropic_api_key == "test-key"
    assert settings.anthropic_model == "custom-model"


@pytest.mark.asyncio
async def test_anthropic_client_translates_project_shapes_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_clients: list[_FakeAsyncAnthropic] = []

    class FakeAsyncAnthropic(_FakeAsyncAnthropic):
        def __init__(self, api_key: str) -> None:
            super().__init__(api_key)
            created_clients.append(self)

    anthropic_module = ModuleType("anthropic")
    anthropic_module.AsyncAnthropic = FakeAsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)

    client = AnthropicLLMClient("test-key", model="test-model", max_tokens=256)

    response = await client.generate(
        [
            LLMMessage(role="user", content="Diagnose PUMP-103."),
        ],
        tools=[
            LLMTool(
                name="get_asset_status",
                description="Get current status.",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        tool_choice=LLMToolChoice(type="auto"),
    )

    assert response == LLMResponse(
        text="Use status.",
        tool_calls=[
            ToolCallRequest(
                id="toolu_1",
                name="get_asset_status",
                input={},
            )
        ],
    )
    assert created_clients[0].api_key == "test-key"
    assert created_clients[0].messages.kwargs == {
        "model": "test-model",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "Diagnose PUMP-103."}],
        "tools": [
            {
                "name": "get_asset_status",
                "description": "Get current status.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "tool_choice": {"type": "auto"},
    }


def test_default_model_uses_initial_sonnet_choice() -> None:
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-4-20250514"


@dataclass(frozen=True)
class _TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return _FakeAnthropicResponse(
            content=[
                _TextBlock(text="Use status."),
                _ToolUseBlock(id="toolu_1", name="get_asset_status", input={}),
            ]
        )


class _FakeAsyncAnthropic:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.messages = _FakeMessages()


@dataclass(frozen=True)
class _FakeAnthropicResponse:
    content: list[Any]
