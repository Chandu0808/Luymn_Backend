"""
Manual energy logger helpers: compute zone watts from Load Schedule
(max_power / high_end_trim) + live zone Level/SwitchedLevel, then roll up to areas.
"""
from __future__ import annotations

from typing import Iterable, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.area import Area
from app.models.events import CurrentAreaEvent, CurrentZoneEvent
from app.models.zone import Zone


def resolve_zone_level_percent(
    level: Optional[object],
    switched_level: Optional[object] = None,
) -> Optional[float]:
    """
    Resolve a 0–100 load level from LEAP ZoneStatuses.
    Prefers Level; falls back to SwitchedLevel (On/Off) for switched zones.
    """
    if level is not None:
        try:
            return float(level)
        except (TypeError, ValueError):
            pass

    if switched_level is None:
        return None

    token = str(switched_level).strip().lower()
    if token in ("on", "1", "true"):
        return 100.0
    if token in ("off", "0", "false"):
        return 0.0
    return None


def compute_zone_instantaneous_power(
    max_power: Optional[float],
    high_end_trim: Optional[float],
    level: Optional[object] = None,
    switched_level: Optional[object] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Compute (zone_instantaneous_power, zone_instantaneous_max_power) from Load Schedule.

    - zone_instantaneous_max_power = max_power (nameplate watts from Load Schedule)
    - zone_instantaneous_power = (level/100) * (max_power * trim/100)
    - high_end_trim defaults to 100 when missing (CSV default / incomplete schedule)
    """
    if max_power is None:
        return None, None

    level_pct = resolve_zone_level_percent(level, switched_level)
    if level_pct is None:
        return None, None

    try:
        mp = float(max_power)
    except (TypeError, ValueError):
        return None, None

    if high_end_trim is None:
        trim = 100.0
    else:
        try:
            trim = float(high_end_trim)
        except (TypeError, ValueError):
            trim = 100.0

    zone_inst_max = mp
    zone_inst_power = (level_pct / 100.0) * (mp * trim / 100.0)
    return zone_inst_power, zone_inst_max


def rollup_current_area_power_from_zones(db: Session, processor_id: int) -> int:
    """
    Set current_area_status.instantaneous_power / instantaneous_max_power from the
    sum of current_zone_status for each area on this processor.
    Ensures area_id is populated so energy_logger can write area_energy_stats.
    Returns number of areas updated.
    """
    area_ids = [
        r[0]
        for r in db.query(CurrentZoneEvent.area_id)
        .filter(
            CurrentZoneEvent.processor_id == processor_id,
            CurrentZoneEvent.area_id.isnot(None),
        )
        .distinct()
        .all()
    ]
    if not area_ids:
        return 0

    areas_updated = 0
    for area_id in area_ids:
        sums = (
            db.query(
                func.coalesce(func.sum(CurrentZoneEvent.zone_instantaneous_power), 0).label("ip"),
                func.sum(CurrentZoneEvent.zone_instantaneous_max_power).label("imp"),
            )
            .filter(
                CurrentZoneEvent.processor_id == processor_id,
                CurrentZoneEvent.area_id == area_id,
            )
            .first()
        )
        if not sums or (sums.ip is None and sums.imp is None):
            continue

        area = db.query(Area).filter(Area.id == area_id).first()
        if not area or area.code is None:
            continue
        try:
            area_code = int(area.code)
        except (TypeError, ValueError):
            continue

        inst_power = float(sums.ip) if sums.ip is not None else 0.0
        inst_max_power = float(sums.imp) if sums.imp is not None else None
        if inst_max_power is None:
            # No zone has load-schedule max power yet — skip rather than wipe live area power
            continue

        current = db.query(CurrentAreaEvent).filter_by(
            processor_id=processor_id,
            area_code=area_code,
        ).first()

        if current:
            current.instantaneous_power = inst_power
            current.instantaneous_max_power = inst_max_power
            if current.area_id is None:
                current.area_id = area_id
            areas_updated += 1
        else:
            db.add(
                CurrentAreaEvent(
                    processor_id=processor_id,
                    area_id=area_id,
                    area_href=f"/area/{area_code}/status",
                    area_code=area_code,
                    instantaneous_power=inst_power,
                    instantaneous_max_power=inst_max_power,
                )
            )
            areas_updated += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return areas_updated


def _resolve_zone_for_current(db: Session, current: CurrentZoneEvent) -> Optional[Zone]:
    if current.zone_id is not None:
        zone = db.query(Zone).filter(Zone.id == current.zone_id).first()
        if zone:
            return zone
    if current.zone_code is None:
        return None
    return (
        db.query(Zone)
        .filter(
            Zone.processor_id == current.processor_id,
            Zone.code == str(current.zone_code),
        )
        .first()
    )


def recompute_current_zone_powers_from_load_schedule(
    db: Session,
    processor_id: Optional[int] = None,
) -> int:
    """
    Recompute zone_instantaneous_* on current_zone_status using existing Level /
    SwitchedLevel and Zone.max_power / high_end_trim (Load Schedule), then roll up.

    Call after Load Schedule CSV upload and at the start of each energy_logger cycle
    when energy_logger_manual is True.
    """
    q = db.query(CurrentZoneEvent)
    if processor_id is not None:
        q = q.filter(CurrentZoneEvent.processor_id == processor_id)

    rows = q.all()
    if not rows:
        return 0

    updated = 0
    processor_ids: Set[int] = set()

    for current in rows:
        zone = _resolve_zone_for_current(db, current)
        if zone is None:
            continue

        if zone.area_id is not None and current.area_id != zone.area_id:
            current.area_id = zone.area_id
        if current.zone_id is None and zone.id is not None:
            current.zone_id = zone.id

        power, max_p = compute_zone_instantaneous_power(
            zone.max_power,
            zone.high_end_trim,
            current.level,
            current.switched_level,
        )
        if max_p is None:
            continue

        current.zone_instantaneous_power = power
        current.zone_instantaneous_max_power = max_p
        updated += 1
        processor_ids.add(current.processor_id)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    for pid in processor_ids:
        rollup_current_area_power_from_zones(db, pid)

    return updated


def recompute_for_processors(db: Session, processor_ids: Iterable[int]) -> int:
    total = 0
    for pid in sorted({int(p) for p in processor_ids if p is not None}):
        total += recompute_current_zone_powers_from_load_schedule(db, processor_id=pid)
    return total
