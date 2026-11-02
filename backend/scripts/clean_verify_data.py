"""清理端到端验证产生的测试数据（只删名字带固定前缀的条目）。

不碰真实数据：过滤条件写死为验证脚本生成的命名前缀。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

TARGETS = [
    ("teams.db", "teams", "name LIKE '验证组-%'"),
    ("teams.db", "team_members", None),  # 由 teams 级联手工处理
    ("community.db", "projects", "name LIKE '智鲸验证项目-%'"),
]

for db, table, where in TARGETS:
    path = DATA / db
    if not path.exists() or where is None:
        continue
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(f"SELECT id FROM {table} WHERE {where}")
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            print(f"{db}:{table} 无残留")
            continue
        marks = ",".join("?" * len(ids))
        # 团队 / 项目的从属表一并清掉，避免留下悬空引用
        child = {
            "teams": ["team_members"],
            "projects": [
                "project_members",
                "project_stars",
                "project_watches",
                "project_comments",
            ],
        }.get(table, [])
        for child_table in child:
            key = "team_id" if table == "teams" else "project_id"
            conn.execute(
                f"DELETE FROM {child_table} WHERE {key} IN ({marks})", ids
            )
        conn.execute(f"DELETE FROM {table} WHERE id IN ({marks})", ids)
        conn.commit()
        print(f"{db}:{table} 删除 {len(ids)} 条")
    finally:
        conn.close()
