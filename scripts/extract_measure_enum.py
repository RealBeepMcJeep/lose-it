#!/usr/bin/env python3
"""Regenerate the ``FoodMeasurement`` table from the Lose It! APK.

The measure enum travels the wire as a bare ordinal, so the ordinal -> unit
mapping is the one table this SDK cannot read off the API. It lives in the app:
Lose It ships protobuf models under ``com.fitnow.foundation.food.v1``, and the
measure enum is a generated Java enum whose ``<clinit>`` constructs every member
in declaration order — which is the ordinal order.

R8 renames the class (``Lsx8`` as of 18.4.600) but cannot rename the constants,
so the table is still readable. This script reads it, verifies it against the
anchors this SDK confirmed from live wire data, and prints a diff against the
table currently in ``src/lose_it/core/_enums.py``.

Usage::

    # androguard is not a project dependency — run it in a throwaway env:
    uv run --with androguard python scripts/extract_measure_enum.py \\
        ~/loseit-18.4.600.apk

Exit status is 1 if any anchor disagrees, so this can gate an upgrade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

# Ordinals this SDK verified against live wire data *before* the APK table
# existed. The table must reproduce every one of them or it is not trustworthy.
ANCHORS: dict[int, str] = {
    1: "TEASPOON",
    2: "TABLESPOON",
    3: "CUP",
    4: "PIECE",
    5: "EACH",
    8: "GRAM",
    11: "MILLILITER",
    19: "BOTTLE",
    24: "STICK",
    26: "SLICE",
    27: "SERVING",
    33: "SCOOP",
    45: "CONTAINER",
    46: "PACKAGE",
}


def find_enum(dex_bytes: bytes):
    """Return the measure enum class, identified by two of its members."""
    from androguard.core.dex import DEX

    dex = DEX(dex_bytes)
    for cls in dex.get_classes():
        names = {f.get_name() for f in cls.get_fields()}
        if "FOOD_MEASURE_TYPE_PACKAGE" in names and "FOOD_MEASURE_TYPE_STICK" in names:
            return cls
    return None


def read_table(cls) -> dict[int, str]:
    """Read ordinal -> member name out of the enum's ``<clinit>``."""
    clinit = next(m for m in cls.get_methods() if m.get_name() == "<clinit>")
    instructions = list(clinit.get_code().get_bc().get_instructions())

    table: dict[int, str] = {}
    position = 0
    while position < len(instructions):
        instruction = instructions[position]
        output = instruction.get_output()
        if instruction.get_name() == "const-string" and "FOOD_MEASURE_TYPE" in output:
            match = re.search(r'"([^"]+)"', output)
            if match:
                table[len(table)] = match.group(1).replace("FOOD_MEASURE_TYPE_", "")
            position += 1
            continue
        position += 1
    return table


def current_table() -> dict[int, str]:
    """The table as it currently ships, for diffing."""
    try:
        from lose_it.core._enums import FoodMeasurement
    except ImportError:  # SDK not installed in this interpreter — skip the diff
        return {}
    return {int(m): m.name for m in FoodMeasurement}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="path to the Lose It APK")
    parser.add_argument("--json", type=Path, help="also write the table here as JSON")
    args = parser.parse_args()

    raw = args.apk.read_bytes()
    print(f"APK: {args.apk.name}  ({len(raw):,} bytes)")
    print(f"sha256: {hashlib.sha256(raw).hexdigest()}\n")

    archive = zipfile.ZipFile(args.apk)
    table: dict[int, str] = {}
    for name in sorted(n for n in archive.namelist() if re.fullmatch(r"classes\d*\.dex", n)):
        cls = find_enum(archive.read(name))
        if cls is not None:
            print(f"measure enum: {cls.get_name()} in {name}\n")
            table = read_table(cls)
            break
    if not table:
        print("!! measure enum not found — the app changed shape; inspect by hand")
        return 1

    print(f"{len(table)} members (declaration order == wire ordinal)\n")
    for ordinal, member in sorted(table.items()):
        print(f"  {ordinal:>3}  {member}")

    print("\nanchor check (ordinals this SDK confirmed from the wire):")
    failures = 0
    for ordinal, expected in sorted(ANCHORS.items()):
        actual = table.get(ordinal, "<missing>")
        ok = actual == expected
        failures += 0 if ok else 1
        print(
            f"  {ordinal:>3}  expect {expected:<12} got {actual:<12} {'ok' if ok else 'MISMATCH'}"
        )

    shipped = current_table()
    if shipped:
        print("\ndiff vs the table currently in src/lose_it/core/_enums.py:")
        for ordinal in sorted(set(table) | set(shipped)):
            new, old = table.get(ordinal, "—"), shipped.get(ordinal, "—")
            if new != old:
                print(f"  {ordinal:>3}  {old:<14} -> {new}")

    if args.json:
        args.json.write_text(json.dumps(table, indent=1) + "\n")
        print(f"\nwrote {args.json}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
