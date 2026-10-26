"""默认业务数据种子。

只插入缺失的默认编号规则，使用 ``INSERT OR IGNORE``，
不得覆盖用户修改过的规则。

``sequence_width = 4`` 是本项目的初始默认配置，并非提示词包规定的固定值，
用户可在后续系统配置阶段修改。起始流水号由用户在编号时手动输入，不落库。
"""

from __future__ import annotations

import sqlite3

DEFAULT_SEQUENCE_WIDTH = 4

# 三类证书规则键，与提示词包 C.5 的 rule_key 取值一致；显示名称由类型配置提供。
DEFAULT_RULE_KEYS: tuple[str, ...] = ("graduation", "degree", "other")


def seed_defaults(connection: sqlite3.Connection) -> None:
    """补齐缺失的编号规则；已有记录保持不变。"""
    for rule_key in DEFAULT_RULE_KEYS:
        connection.execute(
            """
            INSERT OR IGNORE INTO numbering_rules (
                rule_key, fixed_text_1, fixed_text_2, fixed_text_3, fixed_text_4,
                sequence_width, education_levels
            ) VALUES (?, '', '', '', '', ?, '[]')
            """,
            (rule_key, DEFAULT_SEQUENCE_WIDTH),
        )
