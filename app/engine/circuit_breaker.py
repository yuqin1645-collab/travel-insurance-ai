from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from app.logging_utils import LOGGER, log_extra


class CircuitState(Enum):
    CLOSED = "CLOSED"        # 正常
    OPEN = "OPEN"            # 熔断中
    HALF_OPEN = "HALF_OPEN"  # 探测中


class CircuitBreakerOpen(Exception):
    """熔断器处于 OPEN 状态时抛出，调用方可直接跳过该请求"""
    def __init__(self, name: str, reset_in: float):
        self.name = name
        self.reset_in = reset_in
        super().__init__(f"熔断器[{name}]已打开，{reset_in:.1f}s 后进入探测期")


class CircuitBreaker:
    """
    三态熔断器（CLOSED -> OPEN -> HALF_OPEN -> CLOSED）

    参数：
        name            熔断器名称（如 "openrouter"）
        fail_threshold  连续失败多少次后熔断（默认5次）
        reset_timeout   熔断后多少秒进入 HALF_OPEN 探测期（默认30秒）
        half_open_max   HALF_OPEN 期最多允许几个请求探测（默认1个）
    """

    def __init__(
        self,
        name: str,
        fail_threshold: int = 5,
        reset_timeout: float = 30.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._fail_count = 0
        self._open_since: float = 0.0
        self._half_open_passed = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def _should_attempt(self) -> bool:
        """检查当前是否允许发出请求"""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._open_since
            if elapsed >= self.reset_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_passed = 0
                LOGGER.info(
                    f"熔断器[{self.name}] OPEN -> HALF_OPEN，开始探测",
                    extra=log_extra(stage="circuit_breaker", attempt=0),
                )
                return True
            return False
        if self._state == CircuitState.HALF_OPEN:
            return self._half_open_passed < self.half_open_max
        return False

    def _reset_in(self) -> float:
        return max(0.0, self.reset_timeout - (time.monotonic() - self._open_since))

    async def call(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        通过熔断器执行异步调用。
        - OPEN 状态：直接抛 CircuitBreakerOpen，不发出实际请求
        - 成功：重置失败计数
        - 失败：累计失败计数，达阈值则熔断
        """
        async with self._lock:
            if not self._should_attempt():
                raise CircuitBreakerOpen(self.name, self._reset_in())

        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                self._on_success()
            return result
        except CircuitBreakerOpen:
            raise
        except Exception as e:
            async with self._lock:
                self._on_failure(e)
            raise

    def _on_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_passed += 1
            if self._half_open_passed >= self.half_open_max:
                self._state = CircuitState.CLOSED
                self._fail_count = 0
                LOGGER.info(
                    f"熔断器[{self.name}] HALF_OPEN -> CLOSED，服务恢复",
                    extra=log_extra(stage="circuit_breaker", attempt=0),
                )
        elif self._state == CircuitState.CLOSED:
            self._fail_count = 0

    def _on_failure(self, err: Exception) -> None:
        self._fail_count += 1
        if self._state == CircuitState.HALF_OPEN:
            # 探测期失败，立即回到 OPEN
            self._state = CircuitState.OPEN
            self._open_since = time.monotonic()
            LOGGER.warning(
                f"熔断器[{self.name}] HALF_OPEN -> OPEN（探测失败: {err}），"
                f"继续冷却 {self.reset_timeout}s",
                extra=log_extra(stage="circuit_breaker", attempt=0),
            )
        elif self._state == CircuitState.CLOSED and self._fail_count >= self.fail_threshold:
            self._state = CircuitState.OPEN
            self._open_since = time.monotonic()
            LOGGER.warning(
                f"熔断器[{self.name}] CLOSED -> OPEN（连续失败 {self._fail_count} 次: {err}），"
                f"冷却 {self.reset_timeout}s",
                extra=log_extra(stage="circuit_breaker", attempt=0),
            )

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state.value}, "
            f"fail_count={self._fail_count}, reset_timeout={self.reset_timeout})"
        )


# ── 全局单例（按服务名隔离）──────────────────────────────────────────────
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    fail_threshold: int = 5,
    reset_timeout: float = 30.0,
    half_open_max: int = 1,
) -> CircuitBreaker:
    """获取（或创建）指定名称的全局熔断器实例"""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            fail_threshold=fail_threshold,
            reset_timeout=reset_timeout,
            half_open_max=half_open_max,
        )
    return _breakers[name]
