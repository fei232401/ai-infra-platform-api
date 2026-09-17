import os
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.config import Settings
from src.main import create_app
from src.repository import Database

DEFAULT_TEST_DSN = "postgresql+asyncpg://app:app@127.0.0.1:5432/ai_infra_test"

PLATFORM_DIR = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(os.getenv("SCHEMA_DIR", PLATFORM_DIR / "ai-infra-platform-db"))
MIGRATION_FILE = SCHEMA_DIR / "migrations" / "001_init.sql"
SEED_FILE = SCHEMA_DIR / "seeds" / "001_local_dev.sql"

TRUNCATE_SQL = (
    "TRUNCATE TABLE routing_decision, request_log, backend_health_check, "
    "api_key, backend_model, backend, model RESTART IDENTITY CASCADE"
)


def build_settings() -> Settings:
    return Settings(
        app_env="test",
        database_dsn=os.getenv("TEST_DATABASE_DSN", DEFAULT_TEST_DSN),
        redis_url=os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        db_pool_size=5,
        db_max_overflow=2,
        db_statement_timeout_ms=10000,
        auth_required=False,
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    return build_settings()


async def _execute_script(database: Database, script: str) -> None:
    async with database.engine.begin() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute(script)


async def _truncate(database: Database) -> None:
    await _execute_script(database, TRUNCATE_SQL)


@pytest_asyncio.fixture(scope="session")
async def database(settings: Settings) -> AsyncIterator[Database]:
    if os.getenv("SKIP_DB_TESTS") == "1":
        pytest.skip("SKIP_DB_TESTS=1，跳过数据库集成测试")
    if not MIGRATION_FILE.exists():
        pytest.skip(f"未找到迁移文件: {MIGRATION_FILE}")

    instance = Database(settings)
    try:
        await instance.ping()
    except Exception as exc:
        await instance.dispose()
        pytest.skip(f"PostgreSQL 不可达，跳过数据库集成测试: {exc}")

    script = MIGRATION_FILE.read_text(encoding="utf-8")
    await _execute_script(
        instance,
        "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;",
    )
    await _execute_script(instance, script)

    yield instance
    await instance.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator:
    instance = database.new_session()
    try:
        yield instance
    except Exception:
        await instance.rollback()
        await _truncate(database)
        raise
    else:
        await instance.commit()
        await _truncate(database)
    finally:
        await instance.close()


@pytest_asyncio.fixture
async def client(database: Database, settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    app.state.database = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    await _truncate(database)


@pytest_asyncio.fixture
async def seeded_models(database: Database) -> list[int]:
    from src.repository import ModelRepository

    instance = database.new_session()
    try:
        repo = ModelRepository(instance)
        ids: list[int] = []
        for name, family, params, quant in (
            ("qwen2.5:0.5b", "qwen", "0.5", "q4_K_M"),
            ("qwen2.5:1.5b", "qwen", "1.5", "q4_K_M"),
            ("Qwen2.5-7B-Instruct", "qwen", "7", "fp16"),
        ):
            row = await repo.upsert(
                {
                    "name": name,
                    "family": family,
                    "parameter_billions": params,
                    "quantization": quant,
                }
            )
            ids.append(row.id)
        await instance.commit()
        return ids
    finally:
        await instance.close()
