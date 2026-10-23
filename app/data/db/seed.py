"""默认业务数据种子。

只插入缺失的默认编号规则与流水记录，使用 ``INSERT OR IGNORE``，
不得覆盖用户修改过的规则或推进过的流水值。

``sequence_width = 4`` 是本项目的初始默认配置，并非提示词包规定的固定值，
用户可在后续系统配置阶段修改。
"""

from __future__ import annotations

import sqlite3

DEFAULT_SEQUENCE_WIDTH = 4

# 三类证书规则键与显示名称，与提示词包 C.5 的 rule_key 取值一致。
DEFAULT_NUMBERING_RULES: tuple[tuple[str, str], ...] = (
    ("graduation", "毕结业证书"),
    ("degree", "学位证书"),
    ("other", "其他证书"),
)


def seed_defaults(connection: sqlite3.Connection) -> None:
    """补齐缺失的编号规则与流水记录；已有记录保持不变。"""
    for rule_key, rule_name in DEFAULT_NUMBERING_RULES:
        connection.execute(
            """
            INSERT OR IGNORE INTO numbering_rules (
                rule_key, rule_name,
                fixed_text_1, fixed_text_2, fixed_text_3, fixed_text_4,
                sequence_width, enabled
            ) VALUES (?, ?, '', '', '', '', ?, 1)
            """,
            (rule_key, rule_name, DEFAULT_SEQUENCE_WIDTH),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO number_sequences (sequence_key, next_value)
            VALUES (?, 1)
            """,
            (rule_key,),
        )
