from __future__ import annotations

from typing import Any

from app.logging_utils import LOGGER, log_extra
from app.engine.pipeline_labels import PipelineLabels, DEFAULT_LABELS


def log_stage(
    *,
    forceid: str,
    index: int,
    total: int,
    stage_key: str,
    labels: PipelineLabels = DEFAULT_LABELS,
    extra_msg: str = "",
) -> None:
    title = labels.title(stage_key)
    msg = f"[{index}/{total}] {title}..." if not extra_msg else f"[{index}/{total}] {title}: {extra_msg}"
    LOGGER.info(msg, extra=log_extra(forceid=forceid, stage=stage_key, attempt=0))

