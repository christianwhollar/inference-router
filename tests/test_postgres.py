import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from router.ledger import BudgetExceeded
from router.postgres import PostgresLedger


@pytest.mark.skipif(
    not os.getenv("PGVECTOR_TEST_DSN"),
    reason="Set PGVECTOR_TEST_DSN for shared PostgreSQL quota test",
)
def test_shared_quota_across_instances():
    dsn = os.environ["PGVECTOR_TEST_DSN"]
    ledger = PostgresLedger(dsn, 100)
    other = PostgresLedger(dsn, 100)
    tenant = "test-" + str(uuid.uuid4())

    def reserve(i):
        try:
            (ledger if i % 2 else other).reserve(tenant, 30)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 3
    assert other.spent(tenant) == 90
