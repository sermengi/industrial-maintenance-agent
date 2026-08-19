from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from maintenance_agent.orchestration.state import ErrorRecord

RetryValueT = TypeVar("RetryValueT")
AsyncSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryResult[RetryValueT]:
    value: RetryValueT
    attempts: list[ErrorRecord]


class RetryExhaustedError(Exception):
    def __init__(self, message: str, attempts: list[ErrorRecord], cause: Exception) -> None:
        super().__init__(message)
        self.message = message
        self.attempts = attempts
        self.__cause__ = cause


async def with_retry[RetryValueT](
    fn: Callable[[], Awaitable[RetryValueT]],
    *,
    max_attempts: int,
    delay_seconds: float,
    sleep: AsyncSleep,
    error_code: str,
    node: str | None,
) -> RetryResult[RetryValueT]:
    attempts: list[ErrorRecord] = []
    last_error: Exception | None = None

    for attempt_number in range(1, max_attempts + 1):
        try:
            return RetryResult(value=await fn(), attempts=attempts)
        except Exception as exc:
            last_error = exc
            attempts.append(
                ErrorRecord(
                    code=error_code,
                    message=str(exc),
                    node=node,
                    recoverable=True,
                )
            )
            if attempt_number < max_attempts:
                await sleep(delay_seconds)

    if last_error is None:
        raise RuntimeError("Retry loop exhausted without recording an exception.")
    raise RetryExhaustedError(str(last_error), attempts, last_error)
