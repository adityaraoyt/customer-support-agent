import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@lru_cache
def load_customers() -> list[dict[str, Any]]:
    with (DATA_DIR / "customers.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_policy() -> str:
    return (DATA_DIR / "refund_policy.md").read_text(encoding="utf-8")
