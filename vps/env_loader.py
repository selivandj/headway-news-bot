from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def project_root_for(base_dir: str | Path) -> Path:
    """Return the deployment root for both repo-style and flat VPS layouts."""
    base = Path(base_dir).resolve()
    if base.name == "vps":
        return base.parent
    return base


def headway_env_paths(base_dir: str | Path) -> list[Path]:
    """Return supported .env locations in load order."""
    root = project_root_for(base_dir)
    candidates = [root / ".env", root / "vps" / ".env"]
    seen: set[Path] = set()
    paths: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)
    return paths


def load_headway_env(base_dir: str | Path, override: bool = False) -> list[Path]:
    """Load root .env first, then vps/.env if present; never fail if missing."""
    loaded: list[Path] = []
    for path in headway_env_paths(base_dir):
        if path.exists():
            load_dotenv(path, override=override)
            loaded.append(path)
    return loaded
