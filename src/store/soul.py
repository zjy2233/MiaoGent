"""SoulManager and ProfileManager for loading and saving JSON config files."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.core.miaogent_home import get_data_path


class ProfileManager:
    """Manages loading and saving of user profile data."""

    DEFAULT_PROFILE: dict = {"version": 1}

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else get_data_path("profile.json")

    def load(self) -> dict:
        if not self.path.exists():
            return self.DEFAULT_PROFILE.copy()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self.DEFAULT_PROFILE.copy()

    def save(self, profile: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.path))

    def set(self, key: str, value: object, source: str) -> None:
        profile = self.load()
        profile[key] = value
        profile[f"{key}_source"] = source
        self.save(profile)

    def unset(self, key: str) -> None:
        profile = self.load()
        profile.pop(key, None)
        profile.pop(f"{key}_source", None)
        self.save(profile)

    def merge(self, updates: dict, source: str = "discovered") -> None:
        profile = self.load()
        for key, value in updates.items():
            source_key = f"{key}_source"
            existing_source = profile.get(source_key, "")
            if existing_source == "explicit":
                continue
            profile[key] = value
            profile[source_key] = source
        self.save(profile)


class SoulManager:
    """Manages loading and saving of soul.json file."""

    DEFAULT_SOUL: dict = {"version": 1, "description": ""}

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else get_data_path("soul.json")

    def load(self) -> dict:
        if not self.path.exists():
            return self.DEFAULT_SOUL.copy()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self.DEFAULT_SOUL.copy()

    def save(self, soul: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(soul, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.path))
