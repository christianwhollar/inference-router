"""The same accounting state machine backed by PostgreSQL for shared replicas."""

from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from .ledger import Accounting


class Connection:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, parameters=()):
        return self.db.execute(sql.replace("?", "%s"), parameters)


class PostgresLedger(Accounting):
    table = "router_usage"
    lock_row = " FOR UPDATE"

    def __init__(self, dsn, daily_limit_micros=1_000_000):
        self.dsn, self.limit = dsn, daily_limit_micros
        self.initialize()

    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            yield Connection(db)
