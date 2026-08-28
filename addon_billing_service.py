from __future__ import annotations

"""Paid marketplace-position add-ons, separate from legacy addon requests."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from billing_service import BillingService, now_iso
from marketplace_registry import MARKETPLACE_CODES
from storage.postgres_compat import PostgresConnection
from subscription_service import SubscriptionError


ADDON_CODES = frozenset({"positions_100", "positions_500", "positions_1000"})
ORDER_STATUSES = frozenset({
    "awaiting_payment", "under_review", "payment_rejected", "active",
    "cancelled", "superseded",
})
INVOICE_STATUSES = frozenset({"issued", "paid", "cancelled", "superseded"})
PROOF_STATUSES = frozenset({"under_review", "approved", "rejected"})


class AddonBillingService:
    """Owns the invoice-backed checkout flow for catalog position add-ons."""

    def __init__(
        self,
        db_path: Path,
        *,
        document_root: Path | None = None,
        billing_service: BillingService | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.billing = billing_service or BillingService(
            self.db_path, document_root=document_root,
        )

    def _connect(self) -> Any:
        return self.billing._connect()

    @staticmethod
    def _json(value: Any, fallback: Any) -> Any:
        try:
            return json.loads(str(value or ""))
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _invoice_dict(self, row: Any) -> dict[str, Any]:
        value = dict(row)
        value["seller"] = self._json(value.pop("seller_snapshot_json", "{}"), {})
        value["buyer"] = self._json(value.pop("buyer_snapshot_json", "{}"), {})
        value["line_items"] = self._json(value.pop("line_items_json", "[]"), [])
        # InvoicePDFService uses the established subscription-invoice names.
        value["subtotal_amount"] = float(value.get("total_price") or 0)
        value["vat_amount"] = 0.0
        value["total_amount"] = float(value.get("total_price") or 0)
        value["months_count"] = 1
        return value

    def _order_dict(self, conn: Any, row: Any) -> dict[str, Any]:
        value = dict(row)
        invoice = conn.execute(
            "SELECT * FROM tenant_addon_invoices WHERE order_id=?",
            (int(value["id"]),),
        ).fetchone()
        proof = conn.execute(
            "SELECT * FROM tenant_addon_payment_proofs WHERE order_id=? ORDER BY id DESC LIMIT 1",
            (int(value["id"]),),
        ).fetchone()
        value["invoice"] = self._invoice_dict(invoice) if invoice else None
        value["payment_proof"] = dict(proof) if proof else None
        return value

    @staticmethod
    def _require_quantity(value: int) -> int:
        quantity = int(value)
        if not 1 <= quantity <= 100:
            raise SubscriptionError("Количество add-on пакетов должно быть от 1 до 100.")
        return quantity

    @staticmethod
    def _begin(conn: Any) -> None:
        if not isinstance(conn, PostgresConnection):
            conn.execute("BEGIN IMMEDIATE")

    def _supplier(self) -> dict[str, Any]:
        supplier = self.billing.supplier_settings()
        if not supplier.get("is_complete"):
            raise SubscriptionError("Не заполнены реквизиты поставщика для выставления счёта.")
        return supplier

    @staticmethod
    def _buyer(tenant: Any) -> dict[str, Any]:
        return {
            "name": str(tenant["name"] or ""),
            "registration_number": str(tenant["registration_number"] or ""),
            "legal_address": str(tenant["legal_address"] or ""),
            "address": str(tenant["actual_address"] or tenant["legal_address"] or ""),
            "email": str(tenant["contact_email"] or ""),
            "phone": str(tenant["contact_phone"] or ""),
        }

    def _render_invoice_pdf(self, invoice_id: int) -> dict[str, Any]:
        """Use the established invoice PDF generator and safe output handling."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM tenant_addon_invoices WHERE id=?", (int(invoice_id),)
            ).fetchone()
            if not row:
                raise SubscriptionError("Счёт add-on не найден.")
            invoice = self._invoice_dict(row)
            existing_path = str(invoice.get("pdf_path") or "").strip()
            existing_sha = str(invoice.get("pdf_sha256") or "").strip()
        finally:
            conn.close()

        if existing_path or existing_sha:
            path = self.billing._normalize_invoice_file(existing_path)
            if not path.is_file():
                raise SubscriptionError("Нарушена целостность PDF счёта add-on.")
            actual_sha = self.billing._file_sha256(path)
            if actual_sha != existing_sha:
                raise SubscriptionError("Нарушена целостность PDF счёта add-on.")
            return {"invoice": invoice, "path": path, "sha256": actual_sha}

        service = self.billing.invoice_pdf_service or self.billing._default_invoice_pdf_service(invoice)
        try:
            generated = service.generate(self.billing._invoice_pdf_payload(invoice))
        except Exception as exc:
            raise SubscriptionError("Не удалось сформировать PDF счёта add-on.") from exc
        path = self.billing._normalize_invoice_file(str(generated.get("path") or ""))
        if not path.is_file():
            raise SubscriptionError("Сформированный PDF счёта add-on не найден.")
        actual_sha = self.billing._file_sha256(path)
        relative_path = self.billing._relative_invoice_file(path)
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE tenant_addon_invoices SET pdf_path=?,pdf_sha256=?,updated_at=?
                   WHERE id=? AND pdf_path='' AND pdf_sha256=''""",
                (relative_path, actual_sha, now_iso(), int(invoice_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"invoice": invoice, "path": path, "sha256": actual_sha}

    def create_order(
        self,
        tenant_id: int,
        addon_code: str,
        marketplace_code: str,
        quantity: int,
        actor_user_id: int,
    ) -> dict[str, Any]:
        code = str(addon_code or "").strip().casefold()
        marketplace = str(marketplace_code or "").strip().casefold()
        if code not in ADDON_CODES:
            raise SubscriptionError("Для оплаты доступны только пакеты positions_100, positions_500 и positions_1000.")
        if marketplace not in MARKETPLACE_CODES:
            raise SubscriptionError("Укажите поддерживаемый marketplace для add-on пакета.")
        quantity_value = self._require_quantity(quantity)
        supplier = self._supplier()
        stamp = now_iso()
        conn = self._connect()
        try:
            self._begin(conn)
            tenant = conn.execute(
                """SELECT id,name,registration_number,contact_email,contact_phone,
                          legal_address,actual_address,status
                   FROM tenants WHERE id=?""",
                (int(tenant_id),),
            ).fetchone()
            if not tenant:
                raise SubscriptionError("Компания не найдена.")
            subscription = conn.execute(
                """SELECT ends_at FROM tenant_subscriptions
                   WHERE tenant_id=? AND status='active'
                   ORDER BY ends_at DESC LIMIT 1""",
                (int(tenant_id),),
            ).fetchone()
            if not subscription or not str(subscription["ends_at"] or "").strip():
                raise SubscriptionError("Для покупки add-on требуется активный пакет.")
            addon = conn.execute(
                """SELECT * FROM subscription_addons
                   WHERE code=? AND is_active=1 AND is_public=1""",
                (code,),
            ).fetchone()
            if not addon:
                raise SubscriptionError("Add-on пакет недоступен.")
            unit_price = float(addon["price_amount"])
            total_price = unit_price * quantity_value
            cursor = conn.execute(
                """INSERT INTO tenant_addon_orders(
                       tenant_id,addon_id,addon_code,marketplace_code,positions,quantity,
                       unit_price,total_price,currency,valid_until,status,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,'awaiting_payment',?,?,?)""",
                (int(tenant_id), int(addon["id"]), code, marketplace,
                 int(addon["extra_positions"]), quantity_value, unit_price, total_price,
                 str(addon["currency"]), str(subscription["ends_at"]), int(actor_user_id), stamp, stamp),
            )
            order_id = int(cursor.lastrowid)
            due_days = int(supplier.get("invoice_due_days") or 5)
            due_at = (datetime.now().astimezone() + timedelta(days=due_days)).isoformat(timespec="seconds")
            invoice_number = self.billing._next_invoice_number(conn, datetime.now().astimezone())
            lines = [{
                "name": str(addon["name"]), "quantity": quantity_value,
                "unit_price": unit_price, "total_amount": total_price,
                "currency": str(addon["currency"]), "marketplace": marketplace,
                "positions": int(addon["extra_positions"]),
            }]
            invoice_cursor = conn.execute(
                """INSERT INTO tenant_addon_invoices(
                       order_id,tenant_id,invoice_number,status,unit_price,total_price,currency,
                       seller_snapshot_json,buyer_snapshot_json,line_items_json,issued_at,due_at,
                       created_by,created_at,updated_at
                   ) VALUES(?,?,?,'issued',?,?,?,?,?,?,?,?,?,?,?)""",
                (order_id, int(tenant_id), invoice_number, unit_price, total_price,
                 str(addon["currency"]), json.dumps(supplier, ensure_ascii=False),
                 json.dumps(self._buyer(tenant), ensure_ascii=False), json.dumps(lines, ensure_ascii=False),
                 stamp, due_at, int(actor_user_id), stamp, stamp),
            )
            invoice_id = int(invoice_cursor.lastrowid)
            conn.commit()
            row = conn.execute("SELECT * FROM tenant_addon_orders WHERE id=?", (order_id,)).fetchone()
            result = self._order_dict(conn, row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._render_invoice_pdf(invoice_id)
        return self.get_order(order_id, tenant_id=int(tenant_id)) or result

    def catalog(self) -> list[dict[str, Any]]:
        """Return only the three immutable position packages sold by this flow."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT code,name,description,extra_positions,price_amount,currency,display_order
                   FROM subscription_addons
                   WHERE code IN ('positions_100','positions_500','positions_1000')
                     AND is_active=1 AND is_public=1
                   ORDER BY display_order,code"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def list_orders(self, tenant_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM tenant_addon_orders WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                (int(tenant_id), max(1, min(int(limit), 500))),
            ).fetchall()
            return [self._order_dict(conn, row) for row in rows]
        finally:
            conn.close()

    def get_order(self, order_id: int, *, tenant_id: int | None = None) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            query = "SELECT * FROM tenant_addon_orders WHERE id=?"
            params: list[Any] = [int(order_id)]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(int(tenant_id))
            row = conn.execute(query, tuple(params)).fetchone()
            return self._order_dict(conn, row) if row else None
        finally:
            conn.close()

    def reissue(
        self, order_id: int, actor_user_id: int, *, tenant_id: int | None = None,
        marketplace_code: str | None = None, addon_code: str | None = None,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        previous = self.get_order(int(order_id), tenant_id=tenant_id)
        if not previous:
            raise SubscriptionError("Заказ add-on не найден.")
        if previous["status"] not in {"awaiting_payment", "payment_rejected"}:
            raise SubscriptionError("Этот заказ add-on уже нельзя перевыставить.")
        invoice = previous.get("invoice") or {}
        conn = self._connect()
        try:
            self._begin(conn)
            conn.execute(
                "UPDATE tenant_addon_orders SET status='superseded',updated_at=? WHERE id=? AND status IN ('awaiting_payment','payment_rejected')",
                (now_iso(), int(order_id)),
            )
            conn.execute(
                "UPDATE tenant_addon_invoices SET status='superseded',cancelled_by=?,cancelled_at=?,cancel_reason=?,updated_at=? WHERE order_id=? AND status='issued'",
                (int(actor_user_id), now_iso(), "Addon order reissued", now_iso(), int(order_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        replacement = self.create_order(
            int(previous["tenant_id"]), str(addon_code or previous["addon_code"]),
            str(marketplace_code or previous["marketplace_code"]),
            int(previous["quantity"] if quantity is None else quantity), int(actor_user_id),
        )
        conn = self._connect()
        try:
            conn.execute("UPDATE tenant_addon_orders SET superseded_by=?,updated_at=? WHERE id=?", (int(replacement["id"]), now_iso(), int(order_id)))
            conn.commit()
        finally:
            conn.close()
        return replacement

    def upload_payment_proof(
        self, order_id: int, tenant_id: int, actor_user_id: int, *,
        original_filename: str, mime_type: str, content: bytes,
    ) -> dict[str, Any]:
        filename = Path(str(original_filename or "").replace("\\", "/")).name.strip()
        if not filename or len(filename) > 255:
            raise SubscriptionError("Некорректное имя платёжного документа.")
        if not isinstance(content, bytes):
            content = bytes(content)
        extension, canonical_mime = self.billing._validate_payment_proof_content(filename, mime_type, content)
        order = self.get_order(int(order_id), tenant_id=int(tenant_id))
        if not order or not order.get("invoice"):
            raise SubscriptionError("Заказ add-on не найден.")
        if order["status"] not in {"awaiting_payment", "under_review", "payment_rejected"}:
            raise SubscriptionError("Платёжный документ нельзя загрузить для этого заказа add-on.")
        stamp = now_iso()
        folder = self.billing._payment_proof_storage_root() / stamp[:4] / f"tenant-{int(tenant_id)}" / f"addon-order-{int(order_id)}"
        folder.mkdir(parents=True, exist_ok=True)
        path = self.billing._normalize_payment_proof_file(folder / f"{uuid4().hex}{extension}")
        path.write_bytes(content)
        relative_path = self.billing._relative_payment_proof_file(path)
        sha256 = hashlib.sha256(content).hexdigest()
        conn = self._connect()
        try:
            self._begin(conn)
            current = conn.execute(
                """SELECT id,status FROM tenant_addon_payment_proofs
                   WHERE order_id=? AND status='under_review'
                   ORDER BY id DESC LIMIT 1""", (int(order_id),)
            ).fetchone()
            if current:
                conn.execute(
                    """UPDATE tenant_addon_payment_proofs SET status='rejected',review_note=?,reviewed_at=?,updated_at=? WHERE id=?""",
                    ("Replaced by a newer payment proof.", stamp, stamp, int(current["id"])),
                )
            conn.execute(
                """INSERT INTO tenant_addon_payment_proofs(order_id,invoice_id,tenant_id,status,original_filename,stored_path,mime_type,file_size,sha256,uploaded_by,uploaded_at,created_at,updated_at) VALUES(?,?,?,'under_review',?,?,?,?,?,?,?,?,?)""",
                (int(order_id), int(order["invoice"]["id"]), int(tenant_id), filename, relative_path, canonical_mime, len(content), sha256, int(actor_user_id), stamp, stamp, stamp),
            )
            cursor = conn.execute(
                "UPDATE tenant_addon_orders SET status='under_review',updated_at=? WHERE id=? AND tenant_id=? AND status IN ('awaiting_payment','under_review','payment_rejected')",
                (stamp, int(order_id), int(tenant_id)),
            )
            if cursor.rowcount != 1:
                raise SubscriptionError("Статус заказа add-on изменился. Обновите страницу.")
            row = conn.execute("SELECT * FROM tenant_addon_payment_proofs WHERE order_id=? ORDER BY id DESC LIMIT 1", (int(order_id),)).fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            path.unlink(missing_ok=True)
            raise
        finally:
            conn.close()

    def list_payments_for_accountant(self, *, status: str | None = "under_review", limit: int = 100) -> list[dict[str, Any]]:
        if status is not None and status not in PROOF_STATUSES:
            raise SubscriptionError("Некорректный статус платёжного документа.")
        conn = self._connect()
        try:
            query = """SELECT p.*,o.addon_code,o.marketplace_code,o.positions,o.quantity,o.total_price,o.currency,o.status AS order_status,i.invoice_number,i.status AS invoice_status,t.name AS tenant_name FROM tenant_addon_payment_proofs p JOIN tenant_addon_orders o ON o.id=p.order_id JOIN tenant_addon_invoices i ON i.id=p.invoice_id JOIN tenants t ON t.id=p.tenant_id"""
            params: list[Any] = []
            if status is not None:
                query += " WHERE p.status=?"
                params.append(status)
            query += " ORDER BY p.uploaded_at ASC,p.id ASC LIMIT ?"
            params.append(max(1, min(int(limit), 500)))
            return [dict(row) for row in conn.execute(query, tuple(params)).fetchall()]
        finally:
            conn.close()

    def invoice_file(self, invoice_id: int, tenant_id: int) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM tenant_addon_invoices WHERE id=? AND tenant_id=?",
                (int(invoice_id), int(tenant_id)),
            ).fetchone()
            if not row:
                raise SubscriptionError("Счёт add-on не найден.")
            invoice = self._invoice_dict(row)
        finally:
            conn.close()
        path = self.billing._normalize_invoice_file(str(invoice.get("pdf_path") or ""))
        if not path.is_file() or self.billing._file_sha256(path) != str(invoice.get("pdf_sha256") or ""):
            raise SubscriptionError("PDF счёта add-on недоступен или повреждён.")
        return {"invoice": invoice, "path": path}

    def payment_proof_file(self, proof_id: int, *, tenant_id: int | None = None) -> dict[str, Any]:
        conn = self._connect()
        try:
            query = "SELECT * FROM tenant_addon_payment_proofs WHERE id=?"
            params: list[Any] = [int(proof_id)]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(int(tenant_id))
            row = conn.execute(query, tuple(params)).fetchone()
            if not row:
                raise SubscriptionError("Платёжный документ add-on не найден.")
            proof = dict(row)
        finally:
            conn.close()
        path = self.billing._normalize_payment_proof_file(Path(str(proof.get("stored_path") or "")))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != str(proof.get("sha256") or ""):
            raise SubscriptionError("Платёжный документ add-on недоступен или повреждён.")
        return {"proof": proof, "path": path}

    def approve_payment(self, proof_id: int, accountant_user_id: int) -> dict[str, Any]:
        return self._review_payment(int(proof_id), int(accountant_user_id), approve=True, review_note="")

    def reject_payment(self, proof_id: int, accountant_user_id: int, review_note: str = "") -> dict[str, Any]:
        return self._review_payment(int(proof_id), int(accountant_user_id), approve=False, review_note=review_note)

    def _review_payment(self, proof_id: int, actor_user_id: int, *, approve: bool, review_note: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            self._begin(conn)
            proof = conn.execute(
                """SELECT p.*,o.status AS order_status,i.status AS invoice_status
                   FROM tenant_addon_payment_proofs p
                   JOIN tenant_addon_orders o ON o.id=p.order_id
                   JOIN tenant_addon_invoices i ON i.id=p.invoice_id
                   WHERE p.id=?""", (int(proof_id),),
            ).fetchone()
            if not proof:
                raise SubscriptionError("Платёжный документ add-on не найден.")
            if approve and str(proof["status"]) == "approved" and str(proof["order_status"]) == "active" and str(proof["invoice_status"]) == "paid":
                conn.commit()
                return dict(proof)
            if str(proof["status"]) != "under_review":
                raise SubscriptionError("Платёжный документ add-on уже обработан.")
            stamp = now_iso()
            if approve:
                conn.execute("UPDATE tenant_addon_payment_proofs SET status='approved',reviewed_by=?,reviewed_at=?,review_note='',updated_at=? WHERE id=?", (int(actor_user_id), stamp, stamp, int(proof_id)))
                conn.execute("UPDATE tenant_addon_invoices SET status='paid',updated_at=? WHERE id=?", (stamp, int(proof["invoice_id"])))
                conn.execute("UPDATE tenant_addon_orders SET status='active',updated_at=? WHERE id=?", (stamp, int(proof["order_id"])))
            else:
                conn.execute("UPDATE tenant_addon_payment_proofs SET status='rejected',reviewed_by=?,reviewed_at=?,review_note=?,updated_at=? WHERE id=?", (int(actor_user_id), stamp, str(review_note or ""), stamp, int(proof_id)))
                conn.execute("UPDATE tenant_addon_orders SET status='payment_rejected',updated_at=? WHERE id=?", (stamp, int(proof["order_id"])))
            row = conn.execute("SELECT * FROM tenant_addon_payment_proofs WHERE id=?", (int(proof_id),)).fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
