from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step_id: StrictStr
    instruction: StrictStr
    expected_outcome: StrictStr


class PlannerOutput(BaseModel):
    """
    plan + done_when
    """
    model_config = ConfigDict(extra="forbid", strict=True)

    plan: List[PlanStep] = Field(min_length=1)
    done_when: StrictStr
