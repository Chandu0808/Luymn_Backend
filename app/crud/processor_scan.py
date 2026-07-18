"""CRUD helpers for the Layer-3 static-IP processor discovery flow.

Writes only into existing columns on the `processor` table (`ipv4`, `mac`,
`serial`, `server`, `system`, `status`, `handshake_status`). No schema
migrations, no model changes.

Match strategy on upsert:
  1. If `serial` is provided and a row already has it → update that row.
  2. Else if a row already has the same `ipv4` → update that row.
  3. Else insert a new row with status="discovered" and handshake_status=None.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.crud.processor import ensure_processor_table
from app.models.processor import Processor
from app.utils.ip_reachability import (
    DEFAULT_PORTS,
    DEFAULT_TIMEOUT,
    LAP_PORT,
    LEAP_PORT,
    scan_ips_parallel,
)


_DISCOVERED_STATUS = "discovered"


def _derive_server_from_serial(serial: Optional[str]) -> Optional[str]:
    """Build the mDNS-style hostname for a manually-added processor.

    mDNS broadcasts use the form `Lutron-<serial-lower>.local.`, so we
    mirror that exactly. Returns None if no serial is available.
    """
    if not serial:
        return None
    return f"Lutron-{serial.strip().lower()}.local."


def create_or_update_processor(db: Session, data: dict) -> Processor:
    """Upsert a row into the `processor` table.

    `data` may contain any of: ipv4, mac, serial, server, system. Only
    keys with non-empty values are written. status defaults to
    "discovered"; handshake_status stays None so the existing UI's
    Handshake button remains available.

    When the caller provides a serial but no explicit `server`, we
    auto-derive `server` as `Lutron-<serial-lower>.local.` so manually-
    added rows display the same identifier the floor picker expects.
    """
    ensure_processor_table()

    ipv4 = (data.get("ipv4") or "").strip() or None
    serial = (data.get("serial") or "").strip() or None
    mac = (data.get("mac") or "").strip() or None
    server = (data.get("server") or "").strip() or None
    system = (data.get("system") or "").strip() or None

    if not server:
        server = _derive_server_from_serial(serial)

    existing: Optional[Processor] = None
    if serial:
        existing = db.query(Processor).filter(Processor.serial == serial).first()
    if existing is None and ipv4:
        existing = db.query(Processor).filter(Processor.ipv4 == ipv4).first()

    if existing is not None:
        if ipv4:
            existing.ipv4 = ipv4
        if mac:
            existing.mac = mac
        if serial and not existing.serial:
            existing.serial = serial
        # If we now know the serial but the existing row still has no
        # server, backfill it from the serial.
        if not existing.server:
            existing.server = server or _derive_server_from_serial(existing.serial)
        if system and not existing.system:
            existing.system = system
        if not existing.status:
            existing.status = _DISCOVERED_STATUS
        db.commit()
        db.refresh(existing)
        return existing

    new_row = Processor(
        ipv4=ipv4,
        mac=mac,
        serial=serial,
        server=server,
        system=system,
        status=_DISCOVERED_STATUS,
        handshake_status=None,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return new_row


def scan_and_upsert(
    db: Session,
    ips: Iterable[str],
    ports: Sequence[int] = DEFAULT_PORTS,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[dict]:
    """Scan a list of IPs in parallel; for each reachable one, upsert a
    row and return a list of per-IP result dicts ready to be serialized
    into `IpScanResult` items.
    """
    ip_list = [ip for ip in ips if ip]
    if not ip_list:
        return []

    scan_results = scan_ips_parallel(ip_list, ports=ports, timeout=timeout)

    out: List[dict] = []
    for sr in scan_results:
        ip = sr.get("ip")
        ports_map = sr.get("ports", {}) or {}
        item: dict = {
            "ip": ip,
            "reachable": bool(sr.get("reachable")),
            "leap_8081": bool(ports_map.get(LEAP_PORT, False)),
            "lap_8083": bool(ports_map.get(LAP_PORT, False)),
            "latency_ms": sr.get("latency_ms"),
            "error": sr.get("error"),
            "processor_id": None,
            "serial": None,
            "handshake_status": None,
        }

        if item["reachable"]:
            try:
                row = create_or_update_processor(db, {"ipv4": ip})
                item["processor_id"] = row.id
                item["serial"] = row.serial
                item["handshake_status"] = row.handshake_status
            except Exception as e:  # never let one DB error sink the whole batch
                item["error"] = f"db_error: {e}"

        out.append(item)

    return out
