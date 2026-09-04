from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from subscription_service import SubscriptionService


def activate_legacy_subscription(
    db_path: str | Path,
    tenant_id: int,
    *,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Persist the backwards-compatible Legacy entitlement for a historical test."""
    service = SubscriptionService(Path(db_path))
    conn = service._connect()
    try:
        existing = conn.execute(
            """SELECT s.*
               FROM tenant_subscriptions s
               JOIN subscription_plans p ON p.id=s.plan_id
               WHERE s.tenant_id=? AND s.status='active' AND p.code='legacy'
               ORDER BY s.id DESC LIMIT 1""",
            (int(tenant_id),),
        ).fetchone()
        if existing:
            return dict(existing)

        plan = conn.execute(
            "SELECT * FROM subscription_plans WHERE code='legacy'"
        ).fetchone()
        if not plan:
            raise AssertionError("Legacy subscription plan was not seeded")

        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="seconds")
        ends_at = (now + timedelta(days=3650)).isoformat(timespec="seconds")
        snapshot = json.dumps(dict(plan), ensure_ascii=False, default=str)
        cursor = conn.execute(
            """INSERT INTO tenant_subscriptions(
                   tenant_id,plan_id,status,requested_by,requested_at,
                   reviewed_by,reviewed_at,starts_at,ends_at,price_amount,
                   currency,term_days,plan_snapshot_json,review_note,
                   created_at,updated_at
               ) VALUES(?,?,'active',?,?,?,?,?,?,0,'KZT',3650,?,?,?,?)""",
            (
                int(tenant_id),
                int(plan["id"]),
                actor_user_id,
                stamp,
                actor_user_id,
                stamp,
                stamp,
                ends_at,
                snapshot,
                "Explicit historical Legacy test fixture",
                stamp,
                stamp,
            ),
        )
        conn.execute(
            "UPDATE tenants SET plan_code='legacy',updated_at=? WHERE id=?",
            (stamp, int(tenant_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tenant_subscriptions WHERE id=?",
            (int(cursor.lastrowid),),
        ).fetchone()
        if not row:
            raise AssertionError("Legacy subscription fixture was not persisted")
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def activate_trial_subscription(
    db_path: str | Path,
    tenant_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    """Activate Trial through the normal request and review lifecycle."""
    service = SubscriptionService(Path(db_path))
    requested = service.request_plan(
        int(tenant_id),
        "trial",
        int(actor_user_id),
    )
    return service.review_subscription(
        int(requested["id"]),
        "approved",
        int(actor_user_id),
        review_note="Explicit Trial test fixture",
    )
