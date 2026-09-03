"""Short-lived signed proof for verified self-service marketplace sources."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from itsdangerous import BadData, URLSafeSerializer

from config import get_secret_key


OZON_VERIFICATION_PROOF_TTL_SECONDS = 10 * 60
_OZON_VERIFICATION_PROOF_SALT = "spyon-ozon-source-verification-v1"


class MarketplaceVerificationProofError(ValueError):
    """The supplied verification proof is missing, invalid, or expired."""


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(
        get_secret_key(),
        salt=_OZON_VERIFICATION_PROOF_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def issue_ozon_verification_proof(
    *,
    tenant_id: int,
    actor_user_id: int,
    marketplace_code: str,
    normalized_source: str,
    verified_source: dict[str, Any],
) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": int(tenant_id),
        "actor_user_id": int(actor_user_id),
        "marketplace_code": str(marketplace_code),
        "normalized_source": str(normalized_source),
        "canonical_seller_id": str(verified_source["seller_identifier"]),
        "canonical_seller_url": str(verified_source["seller_url"]),
        "seller_name": str(verified_source["seller_name"]),
        "verification_state": "verified",
        "catalogue_empty": bool(verified_source.get("catalogue_empty")),
        "source_scope": str(verified_source.get("source_scope") or "seller"),
        "product_id": str(verified_source.get("product_id") or ""),
        "product_slug": str(verified_source.get("product_slug") or ""),
        "issued_at": now,
        "expires_at": now + OZON_VERIFICATION_PROOF_TTL_SECONDS,
    }
    return str(_serializer().dumps(payload))


def verify_ozon_verification_proof(
    proof: str,
    *,
    tenant_id: int,
    actor_user_id: int,
    marketplace_code: str,
    normalized_source: str,
) -> dict[str, Any]:
    try:
        payload = _serializer().loads(str(proof or ""))
    except BadData as exc:
        raise MarketplaceVerificationProofError("Invalid verification proof.") from exc
    if not isinstance(payload, dict):
        raise MarketplaceVerificationProofError("Invalid verification proof payload.")
    try:
        expired = int(payload.get("expires_at") or 0) <= int(time.time())
    except (TypeError, ValueError) as exc:
        raise MarketplaceVerificationProofError("Invalid verification proof expiry.") from exc
    if expired:
        raise MarketplaceVerificationProofError("Verification proof expired.")
    expected = {
        "tenant_id": int(tenant_id),
        "actor_user_id": int(actor_user_id),
        "marketplace_code": str(marketplace_code),
        "normalized_source": str(normalized_source),
        "verification_state": "verified",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise MarketplaceVerificationProofError("Verification proof context mismatch.")
    if not all(
        str(payload.get(key) or "").strip()
        for key in ("canonical_seller_id", "canonical_seller_url", "seller_name")
    ):
        raise MarketplaceVerificationProofError("Verification proof has no seller identity.")
    return payload
