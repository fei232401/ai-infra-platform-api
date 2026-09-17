# -*- coding: utf-8 -*-
"""把 db 仓的迁移应用到交付库。

为什么放在 CI 里跑，而不是人手跑：
  db 仓是 schema 的**唯一契约来源**。让流水线在部署前把这份契约应用到目标库，
  「代码」和「表结构」才是同一根时间线上的东西；人手跑迁移，两者迟早错位。

幂等：`schema_migration` 表已存在就跳过 —— 约定是迁移只增不改。
（真要重来：删库或 DROP SCHEMA public CASCADE 后重跑。）

环境变量：
  MIGRATE_DSN  交付库的 DSN，形如 postgresql+asyncpg://user:pw@host:5432/db
  SCHEMA_DIR   已 clone 的 db 仓目录（默认 ai-infra-platform-db）
"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg

MIGRATION_REL = "migrations/001_init.sql"


def dsn_to_kwargs(dsn: str) -> dict:
    """SQLAlchemy 风格的 +asyncpg 前缀 asyncpg 不认，要剥掉再拆。"""
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(dsn)
    if not parts.hostname:
        raise SystemExit("MIGRATE_DSN 解析不出主机名: %r" % dsn)
    return {
        "host": parts.hostname,
        "port": parts.port or 5432,
        "user": unquote(parts.username or "postgres"),
        "password": unquote(parts.password or ""),
        "database": (parts.path or "/postgres").lstrip("/") or "postgres",
        "timeout": 30,
    }


async def main() -> int:
    dsn = os.environ.get("MIGRATE_DSN")
    if not dsn:
        print("MIGRATE_DSN 未设置，跳过迁移")
        return 0
    schema_dir = Path(os.environ.get("SCHEMA_DIR", "ai-infra-platform-db"))
    migration = schema_dir / MIGRATION_REL
    if not migration.exists():
        print("找不到迁移文件 %s —— schema 契约没就位，判失败" % migration)
        return 1

    kwargs = dsn_to_kwargs(dsn)
    print("目标库: %s@%s:%s/%s" % (kwargs["user"], kwargs["host"], kwargs["port"], kwargs["database"]))

    for attempt in range(1, 21):
        try:
            conn = await asyncpg.connect(**kwargs)
            break
        except Exception as exc:
            print("  第 %d 次连接失败: %s" % (attempt, exc))
            await asyncio.sleep(3)
    else:
        print("连不上目标库，判失败")
        return 1

    try:
        applied = await conn.fetchval("select to_regclass('public.schema_migration') is not null")
        if applied:
            version = await conn.fetchval("select max(version) from schema_migration")
            names = await conn.fetch("select version, name from schema_migration order by version")
            print("迁移表已存在，当前版本=%s，跳过（迁移只增不改）" % version)
            for r in names:
                print("   v%s %s" % (r["version"], r["name"]))
            return 0

        sql = migration.read_text(encoding="utf-8")
        print("应用 %s（%d 字节）..." % (migration, len(sql)))
        await conn.execute(sql)
        version = await conn.fetchval("select max(version) from schema_migration")
        tables = await conn.fetchval(
            "select count(*) from information_schema.tables where table_schema='public'")
        print("完成：schema_version=%s，public 表数=%s" % (version, tables))
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
