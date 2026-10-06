"""Transactional quota reservations with a durable, exactly-once settlement journal."""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class ReservationConflict(Exception):
    pass


class Accounting:
    table = "usage"

    def initialize(self):
        with self.connect() as db:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table}(tenant TEXT, day TEXT, micros BIGINT NOT NULL, PRIMARY KEY(tenant,day))"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS router_reservations(
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, day TEXT NOT NULL,
                model TEXT NOT NULL, request_id TEXT NOT NULL, reserved BIGINT NOT NULL,
                actual BIGINT, state TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL, note TEXT NOT NULL, operator TEXT)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS reservation_tenant ON router_reservations(tenant,created_at)"
            )

    @staticmethod
    def day():
        return datetime.now(timezone.utc).date().isoformat()

    def _reserve(self, db, tenant, micros, day):
        if not isinstance(micros, int) or micros < 0:
            raise ValueError("Reservation must be a nonnegative integer")
        db.execute(
            f"INSERT INTO {self.table} VALUES(?,?,0) ON CONFLICT(tenant,day) DO NOTHING",
            (tenant, day),
        )
        row = db.execute(
            f"UPDATE {self.table} SET micros=micros+? WHERE tenant=? AND day=? AND micros+?<=? RETURNING micros",
            (micros, tenant, day, micros, self.limit),
        ).fetchone()
        if not row:
            raise BudgetExceeded("Daily tenant budget exhausted")

    def reserve(self, tenant, micros):
        day = self.day()
        with self.connect() as db:
            self._reserve(db, tenant, micros, day)
        return day

    def refund(self, tenant, day, micros):
        # Kept for the original low-level API. Service calls use the guarded settlement path.
        if micros < 0:
            raise ValueError("Cannot refund a negative amount")
        with self.connect() as db:
            db.execute(
                f"UPDATE {self.table} SET micros=CASE WHEN micros>=? THEN micros-? ELSE 0 END WHERE tenant=? AND day=?",
                (micros, micros, tenant, day),
            )

    def spent(self, tenant):
        with self.connect() as db:
            row = db.execute(
                f"SELECT micros FROM {self.table} WHERE tenant=? AND day=?", (tenant, self.day())
            ).fetchone()
        return row["micros"] if row else 0

    def begin(self, tenant, model, micros, request_id):
        reservation, day, now = str(uuid.uuid4()), self.day(), time.time()
        with self.connect() as db:
            self._reserve(db, tenant, micros, day)
            db.execute(
                "INSERT INTO router_reservations VALUES(?,?,?,?,?,?,NULL,?,?,?,?,NULL)",
                (
                    reservation,
                    tenant,
                    day,
                    model,
                    request_id,
                    micros,
                    "active",
                    now,
                    now,
                    "provider call started",
                ),
            )
        return reservation

    def uncertain(self, tenant, reservation, reason):
        with self.connect() as db:
            db.execute(
                "UPDATE router_reservations SET state='uncertain',note=?,updated_at=? WHERE id=? AND tenant=? AND state='active'",
                (reason[:200], time.time(), reservation, tenant),
            )

    def settle(self, tenant, reservation, actual, operator=None, note="provider usage verified"):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM router_reservations WHERE id=? AND tenant=?" + self.lock_row,
                (reservation, tenant),
            ).fetchone()
            if not row:
                raise KeyError(reservation)
            if not isinstance(actual, int) or not 0 <= actual <= row["reserved"]:
                raise ValueError("Actual cost must be within the original reservation")
            if row["state"] == "settled":
                if row["actual"] == actual:
                    return dict(row)
                raise ReservationConflict("Reservation already settled with different usage")
            if (
                operator is not None
                and row["state"] == "active"
                and time.time() - row["created_at"] < 120
            ):
                raise ReservationConflict(
                    "An active provider call cannot be manually reconciled yet"
                )
            if operator is None and row["state"] != "active":
                raise ReservationConflict(
                    "An uncertain reservation requires operator reconciliation"
                )
            refund = row["reserved"] - actual
            db.execute(
                f"UPDATE {self.table} SET micros=micros-? WHERE tenant=? AND day=?",
                (refund, tenant, row["day"]),
            )
            db.execute(
                "UPDATE router_reservations SET actual=?,state='settled',updated_at=?,note=?,operator=? WHERE id=? AND tenant=?",
                (actual, time.time(), note[:500], operator, reservation, tenant),
            )
            return dict(
                db.execute(
                    "SELECT * FROM router_reservations WHERE id=? AND tenant=?",
                    (reservation, tenant),
                ).fetchone()
            )

    def reservations(self, tenant, limit=100):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM router_reservations WHERE tenant=? ORDER BY created_at DESC LIMIT ?",
                    (tenant, limit),
                ).fetchall()
            ]


class Ledger(Accounting):
    lock_row = ""

    def __init__(self, path, daily_limit_micros=1_000_000):
        self.path, self.limit = str(path), daily_limit_micros
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
