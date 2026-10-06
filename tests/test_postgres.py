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


@pytest.mark.skipif(not os.getenv("PGVECTOR_TEST_DSN"), reason="Set PGVECTOR_TEST_DSN")
def test_shared_settlement_is_exactly_once():
    dsn = os.environ["PGVECTOR_TEST_DSN"]
    left, right = PostgresLedger(dsn, 1000), PostgresLedger(dsn, 1000)
    tenant = "settlement-" + str(uuid.uuid4())
    reservation = left.begin(tenant, "model", 500, "request")
    left.uncertain(tenant, reservation, "controlled timeout")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda i: (left if i % 2 else right).settle(
                    tenant, reservation, 125, "reviewer", "verified provider usage"
                ),
                range(20),
            )
        )
    assert left.spent(tenant) == right.spent(tenant) == 125
