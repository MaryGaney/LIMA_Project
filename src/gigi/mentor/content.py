"""Loads LIMA mentor content: Assets/mentor/mentor_config.json and the check-in scripts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gigi.core.config import ASSETS_DIR

MENTOR_DIR = ASSETS_DIR / "mentor"
CHECKIN_DIR = MENTOR_DIR / "checkins"
_ID = re.compile(r"^[a-z0-9_]+$")


def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or MENTOR_DIR / "mentor_config.json", encoding="utf-8") as f:
        return json.load(f)


def list_flows() -> list[dict[str, str]]:
    flows = []
    for p in sorted(CHECKIN_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            flows.append({"id": p.stem, "title": data.get("title", p.stem), "description": data.get("description", "")})
        except (OSError, ValueError):
            continue
    return flows


def load_flow(flow_id: str) -> dict[str, Any]:
    if not _ID.match(flow_id or ""):
        raise ValueError(f"invalid check-in id: {flow_id!r}")
    path = CHECKIN_DIR / f"{flow_id}.json"
    with open(path, encoding="utf-8") as f:
        flow = json.load(f)
    flow.setdefault("id", flow_id)
    return flow
