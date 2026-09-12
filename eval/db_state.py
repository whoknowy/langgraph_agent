# -*- coding: utf-8 -*-
"""评估用的数据库状态工具：备份 / 重置 / 还原。

为什么需要：评估会在演示库里真实下单，而且演示账号的历史订单可能被历次回归
跑脏（例如 M1001 累积上百笔订单，会让 get_order_bill 返回巨大上下文、影响
对话效果与 token 成本）。采基线前重置到干净种子状态，结果才可复现。

    python eval/db_state.py backup     # 备份到 data/flight_system.db.evalbak
    python eval/db_state.py reset      # 重置为干净种子数据（幂等，可重复执行）
    python eval/db_state.py restore    # 从备份还原（评估结束后复原演示库）

推荐流程： backup → reset → run_eval --go → restore
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services import db  # noqa: E402


def _backup_path() -> Path:
    return Path(str(db.DB_PATH) + ".evalbak")


def backup() -> Path:
    """用 SQLite 在线备份 API 生成一致快照（比直接复制文件更安全，含 WAL 未落盘数据）。"""
    dst_path = _backup_path()
    src = sqlite3.connect(str(db.DB_PATH))
    dst = sqlite3.connect(str(dst_path))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dst_path


def restore() -> None:
    bak = _backup_path()
    if not bak.exists():
        raise SystemExit(f"⛔ 备份不存在：{bak}（先执行 backup）")
    src = sqlite3.connect(str(bak))
    dst = sqlite3.connect(str(db.DB_PATH))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


def reset() -> None:
    from services.db_seed import reset_database
    reset_database()


def main():
    ap = argparse.ArgumentParser(description="评估用数据库状态工具")
    ap.add_argument("action", choices=["backup", "reset", "restore"])
    args = ap.parse_args()

    if args.action == "backup":
        print(f"💾 已备份：{backup()}")
    elif args.action == "restore":
        restore()
        print(f"♻️ 已从备份还原：{_backup_path()}")
    else:
        reset()
        print("🔄 已重置为干净种子数据")


if __name__ == "__main__":
    main()
