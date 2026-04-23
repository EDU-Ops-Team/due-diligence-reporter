"""Helpers for resolving canonical REBL site identity from an address."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import requests

DEFAULT_REBL_BASE_URL = "https://rebl3.vercel.app"
DEFAULT_REBL_TIMEOUT_SEC = 15.0


@dataclass(frozen=True)
class ReblResolution:
    """Canonical REBL identity lookup result for one address."""

    address_submitted: str = ""
    resolution_status: str = "not_requested"
    site_id: str = ""
    url: str = ""
    matched_by: str = ""
    scored: bool | None = None
    overall: float | None = None
    classification: str | None = None
    lat: float | None = None
    lng: float | None = None
    address_normalized: str | None = None
    error: str = ""

    @classmethod
    def missing_address(cls) -> ReblResolution:
        return cls(resolution_status="missing_address", error="No address available for REBL resolve.")

    @classmethod
    def error_result(cls, address: str, error: str) -> ReblResolution:
        return cls(
            address_submitted=address,
            resolution_status="error",
            error=error.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_resolution_item(address: str, item: Any) -> ReblResolution:
    if not isinstance(item, dict):
        return ReblResolution.error_result(address, "Resolver returned a non-object item.")

    site_id = str(item.get("site_id") or "").strip()
    url = str(item.get("url") or "").strip()
    status = "resolved" if site_id else "not_found"
    return ReblResolution(
        address_submitted=address,
        resolution_status=status,
        site_id=site_id,
        url=url,
        matched_by=str(item.get("matched_by") or "").strip(),
        scored=item.get("scored"),
        overall=item.get("overall"),
        classification=item.get("classification"),
        lat=item.get("lat"),
        lng=item.get("lng"),
        address_normalized=item.get("address_normalized"),
    )


def resolve_addresses(
    addresses: list[str],
    *,
    base_url: str = DEFAULT_REBL_BASE_URL,
    timeout: float = DEFAULT_REBL_TIMEOUT_SEC,
    session: Any = requests,
) -> list[ReblResolution]:
    """Resolve a batch of site addresses to canonical REBL site IDs."""
    payload = [{"address": address.strip()} for address in addresses if address.strip()]
    if not payload:
        return []

    response = session.post(
        f"{base_url.rstrip('/')}/api/resolve",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"REBL resolve {response.status_code}: {response.text[:200]}")

    body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("REBL resolve returned a non-list response.")
    if len(body) != len(payload):
        raise RuntimeError(
            f"REBL resolve returned {len(body)} results for {len(payload)} addresses.",
        )

    submitted = [item["address"] for item in payload]
    return [_parse_resolution_item(address, item) for address, item in zip(submitted, body, strict=True)]


def resolve_address(
    address: str,
    *,
    base_url: str = DEFAULT_REBL_BASE_URL,
    timeout: float = DEFAULT_REBL_TIMEOUT_SEC,
    session: Any = requests,
) -> ReblResolution:
    """Resolve one site address to a canonical REBL site ID."""
    cleaned = address.strip()
    if not cleaned:
        return ReblResolution.missing_address()
    return resolve_addresses(
        [cleaned],
        base_url=base_url,
        timeout=timeout,
        session=session,
    )[0]
