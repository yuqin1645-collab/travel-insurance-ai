from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.engine.pipeline_shared import is_system_failure_reason  # 已迁移到共享层，此处 re-export 保持兼容

# is_system_failure_reason 和 build_stage_system_failure_return 已迁移到 app.engine.pipeline_shared。
# 此处保留 is_system_failure_reason 的 re-export，供旧调用方兼容。
# build_stage_system_failure_return 的调用方应改用 pipeline_shared.build_stage_error_return_from_reason。