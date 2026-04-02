from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class PipelineLabels:
    """
    阶段文案/编号集中管理，避免主流程硬编码。
    未来不同案件类型可提供不同 labels。
    """

    stage_titles: Dict[str, str]

    def title(self, stage_key: str) -> str:
        return self.stage_titles.get(stage_key, stage_key)


DEFAULT_LABELS = PipelineLabels(
    stage_titles={
        "precheck": "阶段1: 快速预检查",
        "ocr": "阶段2: 识别材料",
        "accident": "阶段3: AI事故判责/除外责任",
        "materials": "阶段4: AI材料审核",
        "coverage": "阶段5: AI保障责任检查",
        "compensation": "阶段6: 赔偿金额核算",
    }
)

