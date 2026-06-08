from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


_JSON_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def extract_json_block(text: str) -> Optional[str]:
    """
    Pull the first {...} or [...] block out of a noisy LLM response.
    """
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def _strip_trailing_commas(s: str) -> str:
    # remove trailing commas before } or ]
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


def repair_json(text: str) -> str:
    """
    Best-effort repair:
    - extract JSON block if surrounded by text
    - strip trailing commas
    Note: keep this conservative; do not invent fields.
    """
    block = extract_json_block(text) or text.strip()
    block = _strip_trailing_commas(block)
    return block


def try_parse_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Returns (obj, error). obj is dict/list/etc.
    """
    try:
        return json.loads(text), None
    except Exception as e:
        return None, str(e)


def parse_and_validate(model: Type[T], raw: str) -> T:
    """
    Parse JSON and validate with strict Pydantic model.
    """
    obj, err = try_parse_json(raw)
    if obj is None:
        repaired = repair_json(raw)
        obj, err2 = try_parse_json(repaired)
        if obj is None:
            raise ValueError(f"Invalid JSON. err={err}; err2={err2}")
    try:
        return model.model_validate(obj)
    except ValidationError as ve:
        raise ValueError(f"Schema validation failed: {ve}") from ve


def validate_or_retry(
    model: Type[T],
    llm_call: Callable[[str], str],
    prompt: str,
    max_attempts: int = 3,
) -> T:
    """
    Enforces: every agent response must validate or be auto-retried.
    The retry strategy is simple: re-ask with a strict instruction.
    (In Phase A we provide the mechanism; Phase B can plug real LLM calls.)
    """
    last_err: Optional[str] = None
    strict_suffix = (
        "\n\nReturn ONLY valid JSON that conforms EXACTLY to the schema. "
        "No extra keys. No markdown. No commentary."
    )

    for attempt in range(1, max_attempts + 1):
        raw = llm_call(prompt if attempt == 1 else (prompt + strict_suffix))
        try:
            return parse_and_validate(model, raw)
        except Exception as e:
            last_err = str(e)

    raise RuntimeError(f"validate_or_retry exhausted after {max_attempts} attempts. last_err={last_err}")
