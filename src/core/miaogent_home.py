"""MiaoGent Home Directory — ~/.miaogent/ 路径工具。

管理用户目录下的 ``.miaogent/`` 文件夹，统一存放运行时数据：
skills、sessions、history、配置等。

环境变量 ``MIAOGENT_HOME`` 可覆盖默认路径。
"""

from __future__ import annotations

import os
from pathlib import Path


def get_miaogent_home() -> Path:
    """获取 ``~/.miaogent/`` 路径，自动创建目录结构。

    优先读取 ``MIAOGENT_HOME`` 环境变量（如果设置），
    否则默认 ``~/.miaogent/``。
    """
    raw = os.environ.get("MIAOGENT_HOME")
    if raw:
        home = Path(raw).expanduser().resolve()
    else:
        home = Path.home() / ".miaogent"

    home.mkdir(parents=True, exist_ok=True)

    # 确保子目录存在
    (home / "skills").mkdir(exist_ok=True)

    return home


def get_data_path(name: str) -> Path:
    """返回 ``~/.miaogent/<name>``，兼容旧的 ``data/<name>``。

    当 ``~/.miaogent/<name>`` 不存在但 ``data/<name>`` 存在时，
    优先返回旧的 ``data/<name>`` 路径以实现平滑迁移。
    """
    p = get_miaogent_home() / name
    if not p.exists():
        legacy = Path("data") / name
        if legacy.exists():
            return legacy
    return p
