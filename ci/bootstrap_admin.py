# -*- coding: utf-8 -*-
"""带外引导第一把 admin API Key。

## 为什么必须有这条带外路径

`POST /api/v1/keys` 自身就挂着 `RequireAdmin`（见 src/api/routes/keys.py）。
当 `APP_ENV=production` + `AUTH_REQUIRED=true` 时，没有任何 HTTP 路径能签出**第一把** key ——
签发接口自己也要鉴权，于是拿不到 key 就永远拿不到 key。

这不是本项目的 bug，而是所有「API Key 自助签发」系统的必然后果：
**签发入口必须有一条不经过 HTTP 的引导路径**，否则第一个管理员永远进不来。

## 为什么复用 ApiKeyService 而不是自己拼 SQL

库里存的是 `sha256(secret_pepper + plaintext)`，prefix 是 `plaintext[:8]`。
这两条规则是**服务运行时**的契约。复制一份到引导脚本里，将来只要 pepper 的用法动一下
（换拼接顺序、加分隔符、改哈希算法），引导脚本照样能插入一行「看起来正常」的记录，
但服务校验时永远不通过 —— 而且不报错，只是 401。所以这里直接 import 服务自己的实现。

## 幂等与「明文只有一次」

幂等键是 `name == 'bootstrap-admin'`：已存在就跳过，重复执行不会堆一串 key。

但注意：哈希不可逆。**明文只在第一次执行时打印一次**，之后就再也取不回来了。
如果需要重置：先把旧 key 吊销（`POST /api/v1/keys/{id}/revoke`），
再把本文件的 BOOTSTRAP_NAME 改成新名字，重新引导。

## 用法

集群内（挂了 ai-platform-api-secret 的 Job 里）：

    DATABASE_DSN=... SECRET_PEPPER=... python ci/bootstrap_admin.py

本地（对着能连的库）：

    DATABASE_DSN=postgresql+asyncpg://app:app@127.0.0.1:5432/ai_infra \\
    SECRET_PEPPER=dev-pepper python ci/bootstrap_admin.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BOOTSTRAP_NAME = "bootstrap-admin"
BOOTSTRAP_SCOPES = ["admin", "infer"]

# 既支持「在镜像里 /app 下跑」，也支持「在仓库根目录直接跑」
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402
from src.repository import Database  # noqa: E402
from src.service import ApiKeyService  # noqa: E402


async def main() -> int:
    settings = get_settings()

    if not settings.secret_pepper:
        print("!! SECRET_PEPPER 为空。")
        print("   服务用同一个变量做 key_hash，为空虽然能算出一个哈希，但和线上服务不一致的风险极大。")
        print("   请在环境里显式提供 SECRET_PEPPER（生产环境的值来自 Secret）。")
        return 2

    print("库   = %s" % settings.database_dsn.split("@")[-1])
    print("env  = %s (production=%s)" % (settings.app_env, settings.is_production))
    print("pepper 长度 = %d" % len(settings.secret_pepper))
    print()

    db = Database(settings)
    try:
        async with db.transaction() as session:
            service = ApiKeyService(session, settings)

            rows, total = await service.list(name_like=BOOTSTRAP_NAME, limit=10)
            hit = [r for r in rows if r.name == BOOTSTRAP_NAME]
            if hit:
                print("已存在引导 key，本次跳过（幂等）。现存：")
                for r in hit:
                    print("  id=%s name=%s prefix=%s scopes=%s revoked_at=%s" % (
                        r.id, r.name, r.key_prefix, r.scopes, r.revoked_at))
                print()
                print("明文已不可取回（库里只有哈希）。需要重置就吊销后改 BOOTSTRAP_NAME。")
                return 0

            entity, plaintext = await service.create(
                name=BOOTSTRAP_NAME,
                scopes=BOOTSTRAP_SCOPES,
            )

            print("已签发引导 key：")
            print("  id     = %s" % entity.id)
            print("  name   = %s" % entity.name)
            print("  prefix = %s" % entity.key_prefix)
            print("  scopes = %s" % entity.scopes)
            print()
            print("明文 key 只出现这一次，请立刻保存：")
            print()
            print("PLAINTEXT_API_KEY=%s" % plaintext)
            print()
            print("自检（应返回 200 + 你的 key 信息）：")
            print("  curl -s -H \"X-API-Key: <上面那串>\" http://ai-infra-platform-api:8000/api/v1/keys")
            return 0
    finally:
        await db.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
