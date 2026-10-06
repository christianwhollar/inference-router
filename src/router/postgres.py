from datetime import datetime, timezone
import psycopg
from .ledger import BudgetExceeded


class PostgresLedger:
    """A shared quota ledger for multiple router processes and replicas."""

    def __init__(self, dsn, daily_limit_micros=1_000_000):
        self.dsn = dsn
        self.limit = daily_limit_micros
        with psycopg.connect(dsn) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS router_usage(
                tenant text, day date, micros bigint NOT NULL CHECK(micros>=0),
                PRIMARY KEY(tenant,day))""")

    def reserve(self, tenant, micros):
        if micros < 0:
            raise ValueError("Cannot reserve negative amount")
        day = datetime.now(timezone.utc).date().isoformat()
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "INSERT INTO router_usage VALUES(%s,%s,0) ON CONFLICT DO NOTHING", (tenant, day)
            )
            row = db.execute(
                """UPDATE router_usage SET micros=micros+%s
                WHERE tenant=%s AND day=%s AND micros+%s<=%s RETURNING micros""",
                (micros, tenant, day, micros, self.limit),
            ).fetchone()
            if not row:
                raise BudgetExceeded("Daily tenant budget exhausted")
        return day

    def refund(self, tenant, day, micros):
        if micros < 0:
            raise ValueError("Cannot refund negative amount")
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "UPDATE router_usage SET micros=GREATEST(0,micros-%s) WHERE tenant=%s AND day=%s",
                (micros, tenant, day),
            )

    def spent(self, tenant):
        day = datetime.now(timezone.utc).date().isoformat()
        with psycopg.connect(self.dsn) as db:
            row = db.execute(
                "SELECT micros FROM router_usage WHERE tenant=%s AND day=%s", (tenant, day)
            ).fetchone()
        return row[0] if row else 0
