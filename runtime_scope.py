from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SellerRuntimeScope:
    """Deterministic, non-secret runtime paths for one marketplace account."""

    root: Path
    tenant_id: int
    marketplace_code: str
    tenant_seller_id: int

    @property
    def seller_key(self) -> str:
        return f"s{int(self.tenant_seller_id)}"

    @property
    def base_dir(self) -> Path:
        return (
            Path(self.root)
            / ".runtime"
            / "marketplaces"
            / f"t{int(self.tenant_id)}"
            / str(self.marketplace_code)
            / self.seller_key
        )

    @property
    def profile_dir(self) -> Path:
        return (
            Path(self.root)
            / ".runtime"
            / "browser_profiles"
            / f"t{int(self.tenant_id)}"
            / str(self.marketplace_code)
            / self.seller_key
        )

    @property
    def registry_path(self) -> Path:
        return self.base_dir / "data" / "registry.db"

    @property
    def runs_dir(self) -> Path:
        return self.base_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.base_dir / "reports"

    @property
    def exports_dir(self) -> Path:
        return self.base_dir / "exports"

    @property
    def raw_dir(self) -> Path:
        return self.base_dir / "raw"

    def ensure_directories(self) -> None:
        for path in (
            self.profile_dir,
            self.registry_path.parent,
            self.runs_dir,
            self.reports_dir,
            self.exports_dir,
            self.raw_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def task_resources(self, declared: Iterable[Any]) -> list[str]:
        values = {str(value).strip() for value in declared if str(value).strip()}
        # Reports and backups remain shared application resources. Marketplace
        # browser/API resources become seller scoped so different accounts can
        # progress independently while every operation for one seller remains
        # mutually exclusive.
        shared = {value for value in values if value in {"reports", "backups"}}
        shared.add(
            f"seller:{int(self.tenant_id)}:{self.marketplace_code}:"
            f"{int(self.tenant_seller_id)}"
        )
        return sorted(shared)


def seller_scope(
    root: Path,
    tenant_id: int,
    marketplace_code: str,
    seller: dict[str, Any] | None,
) -> SellerRuntimeScope | None:
    seller_id = int(
        (seller or {}).get("runtime_seller_id")
        or (seller or {}).get("id")
        or 0
    )
    if int(tenant_id or 0) <= 0 or seller_id <= 0:
        return None
    return SellerRuntimeScope(
        Path(root), int(tenant_id), str(marketplace_code), seller_id
    )
