"""Layer-3 static-IP processor discovery routes.

Lives in a separate router (`/processor_discovery/*`) so the existing
`/processor/*` namespace, mDNS discovery and LAP handshake flow stay
unchanged. Reachable IPs are inserted into the same `processor` table,
so they immediately show up in `GET /processor/list_all` and the
existing Handshake button works on them.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.crud.processor_scan import create_or_update_processor, scan_and_upsert
from app.database.session import SessionLocal
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.schemas.processor import (
    IpScanRequest,
    IpScanResult,
    ProcessorCreate,
    ProcessorListAllOut,
)
from app.utils.ip_reachability import is_valid_ipv4, parse_ip_list


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _collect_ips(payload: IpScanRequest) -> List[str]:
    """Merge `ips` + `raw` into a single de-duplicated list of valid IPv4s."""
    out: List[str] = []
    seen: set[str] = set()

    if payload.ips:
        for ip in payload.ips:
            token = (ip or "").strip()
            if is_valid_ipv4(token) and token not in seen:
                seen.add(token)
                out.append(token)

    if payload.raw:
        for ip in parse_ip_list(payload.raw):
            if ip not in seen:
                seen.add(ip)
                out.append(ip)

    return out


@router.post("/scan", response_model=List[IpScanResult])
def scan_processor_ips(
    payload: IpScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[IpScanResult]:
    """Probe a list of static IPs against LEAP/LAP ports and upsert
    reachable ones into the `processor` table.
    """
    ips = _collect_ips(payload)
    if not ips:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one valid IPv4 address via `ips` or `raw`.",
        )

    ports = payload.ports or [8081, 8083]
    results = scan_and_upsert(db, ips, ports=tuple(ports), timeout=payload.timeout)
    return [IpScanResult(**r) for r in results]


@router.post("/manual_add", response_model=ProcessorListAllOut)
def manual_add_processor(
    payload: ProcessorCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProcessorListAllOut:
    """Insert a single processor row by IP (skips the reachability scan).
    Useful when the engineer already knows the device is offline / on a
    VLAN that the API server cannot reach right now but still wants the
    row in the table for later handshake."""
    if not is_valid_ipv4(payload.ipv4):
        raise HTTPException(status_code=400, detail="Invalid IPv4 address")

    row = create_or_update_processor(
        db,
        {
            "ipv4": payload.ipv4,
            "mac": payload.mac,
            "serial": payload.serial,
            "system": payload.system,
        },
    )
    return row
