from __future__ import annotations

from pathlib import Path


def read_local_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Archivo local no encontrado: {p}")
    return p.read_text(encoding="utf-8", errors="replace")
