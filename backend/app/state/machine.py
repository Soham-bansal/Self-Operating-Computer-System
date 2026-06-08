from __future__ import annotations
from enum import Enum

class Phase(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RETRYING = "RETRYING"
    REPLANNING = "REPLANNING"
    PAUSED = "PAUSED"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    DONE = "DONE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
