from __future__ import annotations


def deduplicate_by_fingerprint(items: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    output: list[dict] = []
    duplicates = 0

    for item in items:
        fp = item.get("fingerprint")
        if fp in seen:
            duplicates += 1
            continue
        seen.add(fp)
        output.append(item)

    return output, duplicates
