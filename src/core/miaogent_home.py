"""MiaoGent Home Directory — ~/.miaogent/ 路径工具。

管理用户目录下的 ``.miaogent/`` 文件夹，统一存放运行时数据：
skills、sessions、history、配置等。

环境变量 ``MIAOGENT_HOME`` 可覆盖默认路径。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("miaogent_home")


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
    (home / "temp").mkdir(exist_ok=True)

    return home


def get_temp_dir() -> Path:
    """获取 ``~/.miaogent/temp/`` 路径，自动创建。

    此目录用于存放 Agent 生成的临时脚本文件（.py/.bat/.sh 等），
    不纳入版本控制，可定期清理。
    """
    p = get_miaogent_home() / "temp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_data_path(name: str) -> Path:
    """返回 ``~/.miaogent/<name>`` 路径。"""
    return get_miaogent_home() / name


def cleanup_temp_dir() -> None:
    """清理 ``~/.miaogent/temp/`` 下的所有 Agent 临时脚本。

    在应用退出时调用，确保 Agent 产生的临时文件不残留。
    只删除文件，不删除 temp 目录本身。
    """
    temp_dir = get_temp_dir()
    if not temp_dir.exists():
        return

    count = 0
    for f in temp_dir.iterdir():
        try:
            if f.is_file():
                f.unlink()
                count += 1
            elif f.is_dir():
                shutil.rmtree(f)
                count += 1
        except Exception as e:
            logger.warning("清理临时文件失败: %s — %s", f.name, e)

    if count:
        logger.info("已清理 %d 个 Agent 临时文件（%s）", count, temp_dir)
