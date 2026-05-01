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


def canonical_slug_for_address(
    address: str,
    *,
    fallback: str = "",
    base_url: str = DEFAULT_REBL_BASE_URL,
    timeout: float = DEFAULT_REBL_TIMEOUT_SEC,
    session: Any = requests,
) -> str:
    """Return Rebl's canonical slug for ``address``, or ``fallback`` if unavailable.

    Used by readers/scripts (reconcile, sync_site_roster, etc.) that need to
    know the slug a *publish* would mint. Single source of truth: the
    publisher uses the same Rebl ``site_id``, so any caller that runs this
    helper and gets a non-empty result will agree with the dashboard.

    Returns ``fallback`` (which most callers will pass as ``slugify(title)``)
    when:
      * the address is empty,
      * the network call fails,
      * Rebl returns no ``site_id``.

    Never raises — callers in batch loops should not be aborted by a
    single Rebl miss.
    """
    cleaned = (address or "").strip()
    if not cleaned:
        return fallback
    try:
        result = resolve_address(
            cleaned,
            base_url=base_url,
            timeout=timeout,
            session=session,
        )
    except Exception:  # network/runtime errors should not abort the caller
        return fallback
    return result.site_id.strip() or fallback


def canonical_slugs_for_addresses(
    addresses: list[str],
    *,
    base_url: str = DEFAULT_REBL_BASE_URL,
    timeout: float = DEFAULT_REBL_TIMEOUT_SEC,
    session: Any = requests,
) -> dict[str, str]:
    """Batch-resolve a list of addresses to canonical Rebl slugs.

    Returns ``{address: site_id}`` for every address Rebl could resolve.
    Addresses that fail to resolve (or return an empty ``site_id``) are
    omitted. Empty/whitespace addresses are skipped silently.

    Never raises — a network failure returns ``{}`` so callers can fall
    back to title-based slugs without aborting an entire reconcile/sync run.
    """
    cleaned: list[str] = [a.strip() for a in addresses if a and a.strip()]
    if not cleaned:
        return {}
    try:
        results = resolve_addresses(
            cleaned,
            base_url=base_url,
            timeout=timeout,
            session=session,
        )
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in results:
        slug = (r.site_id or "").strip()
        if slug and r.address_submitted:
            out[r.address_submitted] = slug
    return out
