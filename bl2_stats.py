"""
Codec for the Borderlands 2 StatsData blob (save protobuf field 15).

Challenge *progress* lives here, not in field 38.  Field 38 ("ChallengeList")
is only a registry of challenge definitions with read-only DLC metadata
({1: name, 2: is_from_dlc, 3: dlc_id}); rewriting its sub-fields 2/3 makes
the game mis-attribute or drop stats such as ECHO recorder pickups and boss
kills.

Blob layout (little-endian on PC/Mac):
    uint32 unknown (always 4)
    uint32 size_in_bytes (= 12 * count + 2)
    uint16 count
    count x { uint16 id, uint8 first_one, uint32 total_value,
              uint8 second_one, uint32 previous_value }

The current value of a stat is total_value - previous_value (previous_value
is set when challenges are reset in-game).  Format notes and the challenge
definitions in bl2_challenge_defs.json derive from apocalyptech/borderlands2.
"""
from __future__ import annotations

import json
import os
import struct
from functools import lru_cache
from typing import Any

ENDIAN = "<"
_HEADER = struct.Struct(ENDIAN + "IIH")
_ENTRY = struct.Struct(ENDIAN + "HBIBI")
_DEFS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bl2_challenge_defs.json")
MAX_U32 = 0xFFFFFFFF


class StatsFormatError(ValueError):
    pass


def unpack_stats(data: bytes) -> dict[str, Any]:
    if len(data) < _HEADER.size:
        raise StatsFormatError("Stats blob too short")
    unknown, size_in_bytes, count = _HEADER.unpack_from(data, 0)
    if size_in_bytes + 8 != len(data):
        raise StatsFormatError(f"Stats blob reports {size_in_bytes} bytes but has {len(data) - 8}")
    if count * _ENTRY.size != size_in_bytes - 2:
        raise StatsFormatError(f"Stats blob reports {count} entries but has {size_in_bytes - 2} bytes")
    entries = []
    for i in range(count):
        sid, first_one, total, second_one, prev = _ENTRY.unpack_from(data, _HEADER.size + i * _ENTRY.size)
        entries.append({"id": sid, "first_one": first_one, "total_value": total,
                        "second_one": second_one, "previous_value": prev})
    return {"unknown": unknown, "entries": entries}


def pack_stats(stats: dict[str, Any]) -> bytes:
    entries = stats["entries"]
    out = [_HEADER.pack(stats["unknown"], len(entries) * _ENTRY.size + 2, len(entries))]
    for e in entries:
        out.append(_ENTRY.pack(e["id"], e["first_one"], e["total_value"],
                               e["second_one"], e["previous_value"]))
    return b"".join(out)


def current_value(entry: dict[str, Any]) -> int:
    return max(0, entry["total_value"] - entry["previous_value"])


def set_current_value(entry: dict[str, Any], value: int) -> None:
    """Set the displayed (current) value, keeping previous_value intact."""
    value = max(0, int(value))
    total = entry["previous_value"] + value
    if total > MAX_U32:
        raise ValueError(f"Stat value {value} too large")
    entry["total_value"] = total


@lru_cache(maxsize=1)
def challenge_defs() -> dict[int, dict[str, Any]]:
    with open(_DEFS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {int(c["id"]): c for c in raw["challenges"]}


def level_reached(value: int, levels: list[int]) -> int:
    return sum(1 for t in levels if value >= t)
