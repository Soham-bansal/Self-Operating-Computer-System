from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .action_schema import Action, Expectation, Intent, MatchQuery


class NavigatorOutput(BaseModel):
    """
    intent + atomic action + target match + expect
    """
    model_config = ConfigDict(extra="forbid", strict=False)

    intent: Intent
    action: Action
    target: MatchQuery
    expect: Expectation
