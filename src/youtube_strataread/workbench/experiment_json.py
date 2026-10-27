"""Strict provider JSON with one optional whole-response JSON code-block envelope."""

from __future__ import annotations

import json
import re
from typing import Any

RESPONSE_JSON_CONTRACT = "json-or-single-json-codeblock-v1"


def decode_response_json(text: str) -> Any:
    envelope = re.fullmatch(r"```json\r?\n(.*)\r?\n```", text.strip(), re.DOTALL)
    return json.loads(envelope.group(1) if envelope else text)
