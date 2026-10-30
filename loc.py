"""Load Stellaris Chinese localisation keys into a dict.

Scans localisation/simp_chinese/*.yml and returns {key: "中文"}.
Used by save_parser to turn SPEC_Cevelli_system into a readable name.
"""
from __future__ import annotations

import glob
import os
import re

LOC_DIR = r"D:\SteamLibrary\steamapps\common\Stellaris\localisation\simp_chinese"
_LINE = re.compile(r'^\s*([A-Za-z0-9_.\-\[\]]+)\s*:\s*\d*\s*"((?:[^"\\]|\\.)*)"')


def load(directory: str = LOC_DIR) -> dict[str, str]:
    table: dict[str, str] = {}
    files = glob.glob(os.path.join(directory, "**", "*.yml"), recursive=True)
    for path in files:
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    m = _LINE.match(line)
                    if m:
                        table.setdefault(m.group(1), m.group(2))
        except OSError:
            continue
    return table


def translate(table: dict[str, str], key: str | None, default: str | None = None) -> str | None:
    if not key:
        return default
    if key in table:
        return table[key]
    # generated names append a suffix (SPEC_X_system); the root is in localisation
    for suffix in ("_system", "_planet", "_star", "_pl"):
        if key.endswith(suffix):
            base = table.get(key[: -len(suffix)])
            if base:
                return base
            break
    return default if default is not None else key


if __name__ == "__main__":
    import sys

    t = load()
    print(f"loaded {len(t)} keys from {LOC_DIR}")
    for q in sys.argv[1:] or ["SPEC_Cevelli_system", "HUM1_FLEET_SuthnarsArmada", "shipclass_military"]:
        print(f"  {q} -> {t.get(q, '(missing)')}")
