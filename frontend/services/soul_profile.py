"""Soul/Profile 读写服务。"""

from __future__ import annotations

from src.store.soul import ProfileManager, SoulManager


class SoulProfileService:
    """Soul（AI 角色设定）和 Profile（用户画像）的读写。"""

    def __init__(self, soul_path, profile_path):
        self._soul_path = soul_path
        self._profile_path = profile_path

    def get_soul(self) -> dict:
        return SoulManager(self._soul_path).load()

    def save_soul(self, soul: dict) -> None:
        SoulManager(self._soul_path).save(soul)

    def get_profile(self) -> dict:
        return ProfileManager(self._profile_path).load()

    def save_profile(self, profile: dict) -> None:
        ProfileManager(self._profile_path).save(profile)
