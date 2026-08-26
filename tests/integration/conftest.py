import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    """Connect to the migrated disposable Postgres database created by the test script."""

    database_url = os.environ.get("ATLAS_TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("run integration tests through atlas run test-integration")
    engine = create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_session_factory(postgres_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=postgres_engine, expire_on_commit=False)
