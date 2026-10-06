import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path, daily_limit_micros=1_000_000):
        self.path = str(path)
        self.limit = daily_limit_micros
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage(tenant TEXT, day TEXT, micros INTEGER NOT NULL, PRIMARY KEY(tenant,day))"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, tenant, micros):
        if micros < 0:
            raise ValueError("Cannot reserve a negative amount")
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage VALUES(?,?,0)", (tenant, day))
            spent = db.execute(
                "SELECT micros FROM usage WHERE tenant=? AND day=?", (tenant, day)
            ).fetchone()[0]
            if spent + micros > self.limit:
                raise BudgetExceeded("Daily tenant budget exhausted")
            db.execute(
                "UPDATE usage SET micros=micros+? WHERE tenant=? AND day=?", (micros, tenant, day)
            )
        return day

    def refund(self, tenant, day, micros):
        if micros < 0:
            raise ValueError("Cannot refund a negative amount")
        with self.connect() as db:
            db.execute(
                "UPDATE usage SET micros=MAX(0,micros-?) WHERE tenant=? AND day=?",
                (micros, tenant, day),
            )

    def spent(self, tenant):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            row = db.execute(
                "SELECT micros FROM usage WHERE tenant=? AND day=?", (tenant, day)
            ).fetchone()
        return row[0] if row else 0
