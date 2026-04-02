from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.logging_utils import LOGGER, log_extra
from app.engine.circuit_breaker import CircuitBreakerOpen, get_circuit_breaker

# 连接类错误关键词，命中时使用更长的等待时间
_CONN_ERROR_KEYWORDS = (
    "cannot connect",
    "connection refused",
    "connection reset",
    "ssl",
    "timeout",
    "network",
    "name or service not known",
)


@dataclass
class StageError:
    stage: str
    attempt: int
    error: str


class StageRunner:
    """
    通用阶段执行器：
    - 单阶段内重试（不会重跑整个流程）
    - 记录 ctx["debug"]，便于定位错误发生在哪个 stage/哪次 attempt
    - 统一日志格式（forceid/stage/attempt）
    - 指数退避：每次重试等待时间翻倍，网络错误额外加长
    - 熔断器：连续失败超阈值后暂停请求，冷却后自动探测恢复
    """

    def __init__(self, *, ctx: Dict[str, Any], forceid: str):
        self.ctx = ctx
        self.forceid = forceid or "unknown"
        self.ctx.setdefault("debug", [])

    async def run(
        self,
        stage_name: str,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        max_retries: int = 3,
        retry_sleep: float = 3.0,
        circuit_name: str = "openrouter",
        **kwargs: Any,
    ) -> Tuple[Optional[Any], Optional[Exception]]:
        breaker = get_circuit_breaker(
            circuit_name,
            fail_threshold=5,
            reset_timeout=30.0,
            half_open_max=1,
        )
        last_err: Optional[Exception] = None
        for attempt in range(1, max(1, int(max_retries)) + 1):
            try:
                result = await breaker.call(func, *args, **kwargs)
                return result, None
            except CircuitBreakerOpen as e:
                # 熔断器打开：直接放弃本次，不再重试，等冷却期结束
                LOGGER.warning(
                    f"{stage_name} 熔断器已打开，跳过请求，{e.reset_in:.1f}s 后可重试",
                    extra=log_extra(forceid=self.forceid, stage=stage_name, attempt=attempt),
                )
                try:
                    self.ctx["debug"].append(
                        {"stage": stage_name, "attempt": attempt, "error": f"circuit_open:{e.reset_in:.1f}s"}
                    )
                except Exception:
                    pass
                return None, e
            except Exception as e:
                last_err = e
                try:
                    self.ctx["debug"].append(
                        {"stage": stage_name, "attempt": attempt, "error": str(e)[:200]}
                    )
                except Exception:
                    pass
                if attempt < max_retries:
                    # 指数退避：base_sleep * 2^(attempt-1)，网络连接错误额外×3
                    err_lower = str(e).lower()
                    is_conn_err = any(kw in err_lower for kw in _CONN_ERROR_KEYWORDS)
                    wait = retry_sleep * (2 ** (attempt - 1)) * (3 if is_conn_err else 1)
                    wait = min(wait, 60.0)  # 最长等60秒
                    LOGGER.warning(
                        f"{stage_name} 失败（{'连接错误' if is_conn_err else '一般错误'}），"
                        f"等待 {wait:.1f}s 后重试 (attempt {attempt}/{max_retries})",
                        extra=log_extra(forceid=self.forceid, stage=stage_name, attempt=attempt),
                    )
                    await asyncio.sleep(wait)
        return None, last_err


