# """
# Continuously inject fake listener AreaStatuses for ALL areas in the DB.

# This script is meant for load/demo testing of listener ingestion paths.
# It scans all areas, then periodically calls `app.listener.check_area_occupancy`
# with synthetic `{"AreaStatuses": [...]}` payloads at per-area random intervals.

# Stop with Ctrl+C.

# Examples (run from repo root):

#   python scripts/demo_inject_all_areas_listener.py
#   python scripts/demo_inject_all_areas_listener.py --min-interval 1 --max-interval 5
#   python scripts/demo_inject_all_areas_listener.py --dry-run --max-events 10
# """

# from __future__ import annotations

# import argparse
# import os
# import random
# import sys
# import time
# from dataclasses import dataclass
# from pathlib import Path


# ROOT = Path(__file__).resolve().parents[1]
# if str(ROOT) not in sys.path:
#     sys.path.insert(0, str(ROOT))

# try:
#     from dotenv import load_dotenv

#     for env_file in (ROOT / "environment.env", ROOT / ".env"):
#         if env_file.is_file():
#             load_dotenv(env_file)
#             break
# except ImportError:
#     pass

# if not os.getenv("DATABASE_HOST_URL"):
#     print("ERROR: DATABASE_HOST_URL not set (environment.env or .env).")
#     raise SystemExit(1)


# from app.database.session import SessionLocal
# from app.listener import check_area_occupancy
# from app.models.area import Area


# @dataclass(frozen=True)
# class AreaTarget:
#     area_id: int
#     processor_id: int
#     area_code: int
#     name: str | None


# def _parse_int_code(v) -> int | None:
#     if v is None:
#         return None
#     try:
#         return int(str(v).strip())
#     except (TypeError, ValueError):
#         return None


# def _build_area_status_payload(
#     *,
#     area_code: int,
#     include_power: bool,
#     power_w: float | None,
#     max_power_w: float | None,
#     occupancy: str,
#     scene_id: int,
# ) -> dict:
#     status: dict = {
#         "href": f"/area/{area_code}/status",
#         "OccupancyStatus": occupancy,
#         "CurrentScene": {"href": f"/scene/{scene_id}"},
#     }
#     if include_power:
#         status["InstantaneousPower"] = power_w
#         status["InstantaneousMaxPower"] = max_power_w
#     return {"AreaStatuses": [status]}


# def _load_targets(db, only_area_ids: set[int] | None, only_processor_id: int | None) -> list[AreaTarget]:
#     q = db.query(Area).order_by(Area.id.asc())
#     if only_processor_id is not None:
#         q = q.filter(Area.processor_id == only_processor_id)
#     if only_area_ids:
#         q = q.filter(Area.id.in_(sorted(only_area_ids)))

#     areas = q.all()
#     targets: list[AreaTarget] = []
#     skipped = 0
#     for a in areas:
#         code = _parse_int_code(a.code)
#         if code is None or a.processor_id is None:
#             skipped += 1
#             continue
#         targets.append(
#             AreaTarget(
#                 area_id=int(a.id),
#                 processor_id=int(a.processor_id),
#                 area_code=int(code),
#                 name=getattr(a, "name", None),
#             )
#         )
#     if not targets:
#         raise SystemExit("No injectable areas found (need numeric Area.code and non-null processor_id).")
#     print(f"Loaded {len(targets)} injectable area(s) (skipped {skipped}).")
#     return targets


# def run_injector(
#     *,
#     min_interval_sec: float,
#     max_interval_sec: float,
#     power_min_w: float,
#     power_max_w: float,
#     max_power_w: float,
#     occupied_prob: float,
#     include_power: bool,
#     scene_min: int,
#     scene_max: int,
#     dry_run: bool,
#     max_events: int | None,
#     only_area_ids: set[int] | None,
#     only_processor_id: int | None,
# ) -> None:
#     if min_interval_sec <= 0 or max_interval_sec <= 0 or max_interval_sec < min_interval_sec:
#         raise SystemExit("Invalid interval settings: require 0 < min_interval <= max_interval.")
#     if power_max_w < power_min_w:
#         raise SystemExit("Invalid power range: require power-min <= power-max.")
#     if not (0.0 <= occupied_prob <= 1.0):
#         raise SystemExit("Invalid --occupied-prob: must be within [0, 1].")
#     if scene_min <= 0 or scene_max < scene_min:
#         raise SystemExit("Invalid scene range: require scene-min >= 1 and scene-min <= scene-max.")

#     db = SessionLocal()
#     try:
#         targets = _load_targets(db, only_area_ids=only_area_ids, only_processor_id=only_processor_id)
#         now = time.monotonic()
#         next_fire: dict[AreaTarget, float] = {
#             t: now + random.uniform(min_interval_sec, max_interval_sec) for t in targets
#         }

#         injected = 0
#         print(
#             "Starting injector. Press Ctrl+C to stop.\n"
#             f"  areas={len(targets)}  interval=[{min_interval_sec},{max_interval_sec}]s"
#             f"  include_power={include_power}  dry_run={dry_run}"
#             + (f"  max_events={max_events}" if max_events is not None else "")
#         )

#         while True:
#             now = time.monotonic()
#             due = [t for t, ts in next_fire.items() if ts <= now]

#             if not due:
#                 # Sleep until the nearest scheduled injection (capped for responsiveness to Ctrl+C).
#                 sleep_for = max(0.0, min(next_fire.values()) - now)
#                 time.sleep(min(0.5, sleep_for))
#                 continue

#             for t in due:
#                 if max_events is not None and injected >= max_events:
#                     print(f"Reached max events ({max_events}); exiting.")
#                     return

#                 # Reschedule first to avoid rapid retries if injection raises.
#                 next_fire[t] = time.monotonic() + random.uniform(min_interval_sec, max_interval_sec)

#                 occupancy = "Occupied" if random.random() < occupied_prob else "Unoccupied"
#                 scene_id = random.randint(scene_min, scene_max)

#                 power = None
#                 mp = None
#                 if include_power:
#                     mp = float(max_power_w)
#                     power = float(random.uniform(power_min_w, min(power_max_w, mp)))

#                 payload = _build_area_status_payload(
#                     area_code=t.area_code,
#                     include_power=include_power,
#                     power_w=power,
#                     max_power_w=mp,
#                     occupancy=occupancy,
#                     scene_id=scene_id,
#                 )

#                 label = f"area_id={t.area_id} code={t.area_code} P{t.processor_id}"
#                 if t.name:
#                     label += f" name={t.name!r}"

#                 if dry_run:
#                     injected += 1
#                     print(f"[dry-run] inject #{injected}: {label} occ={occupancy} scene={scene_id} power={power} max={mp}")
#                     continue

#                 try:
#                     check_area_occupancy(payload, db, t.processor_id)
#                     injected += 1
#                     print(f"inject #{injected}: {label} occ={occupancy} scene={scene_id} power={power} max={mp}")
#                 except Exception as e:
#                     db.rollback()
#                     print(f"ERROR injecting {label}: {e!r}")

#     except KeyboardInterrupt:
#         print("\nStopping (Ctrl+C).")
#     finally:
#         db.close()


# def main() -> None:
#     p = argparse.ArgumentParser(description="Inject fake listener AreaStatuses for all areas until Ctrl+C.")
#     p.add_argument("--min-interval", type=float, default=1.0, help="Minimum seconds between injections per area.")
#     p.add_argument("--max-interval", type=float, default=5.0, help="Maximum seconds between injections per area.")
#     p.add_argument("--power-min", type=float, default=0.0, help="Minimum InstantaneousPower (W).")
#     p.add_argument("--power-max", type=float, default=60.0, help="Maximum InstantaneousPower (W).")
#     p.add_argument("--max-power", type=float, default=100.0, help="InstantaneousMaxPower (W).")
#     p.add_argument("--occupied-prob", type=float, default=0.7, help="Probability of Occupied vs Unoccupied.")
#     p.add_argument(
#         "--no-power",
#         action="store_true",
#         help="Do not include power fields (skips energy-related handling in listener).",
#     )
#     p.add_argument("--scene-min", type=int, default=1, help="Minimum scene id for CurrentScene.href (/scene/<id>).")
#     p.add_argument("--scene-max", type=int, default=4, help="Maximum scene id for CurrentScene.href (/scene/<id>).")
#     p.add_argument(
#         "--dry-run",
#         action="store_true",
#         help="Do not call listener; just print what would be injected.",
#     )
#     p.add_argument(
#         "--max-events",
#         type=int,
#         default=None,
#         help="Exit after injecting this many events (useful for testing). Default: run forever.",
#     )
#     p.add_argument(
#         "--only-processor-id",
#         type=int,
#         default=None,
#         help="Optional filter: only inject for areas belonging to this processor_id.",
#     )
#     p.add_argument(
#         "--only-area-ids",
#         type=str,
#         default=None,
#         help="Optional filter: comma-separated Area.id list, e.g. '1,2,5'.",
#     )
#     args = p.parse_args()

#     only_area_ids: set[int] | None = None
#     if args.only_area_ids:
#         try:
#             only_area_ids = {int(x.strip()) for x in args.only_area_ids.split(",") if x.strip()}
#         except ValueError:
#             raise SystemExit("--only-area-ids must be a comma-separated list of integers.")

#     run_injector(
#         min_interval_sec=float(args.min_interval),
#         max_interval_sec=float(args.max_interval),
#         power_min_w=float(args.power_min),
#         power_max_w=float(args.power_max),
#         max_power_w=float(args.max_power),
#         occupied_prob=float(args.occupied_prob),
#         include_power=not bool(args.no_power),
#         scene_min=int(args.scene_min),
#         scene_max=int(args.scene_max),
#         dry_run=bool(args.dry_run),
#         max_events=int(args.max_events) if args.max_events is not None else None,
#         only_area_ids=only_area_ids,
#         only_processor_id=int(args.only_processor_id) if args.only_processor_id is not None else None,
#     )


# if __name__ == "__main__":
#     main()


# """
# Continuously inject fake listener AreaStatuses for ALL areas in the DB.

# - Always sends occupancy + power (no NULL energy values)
# - Power is randomized per event
# - Optional realism: lower power when unoccupied

# Stop with Ctrl+C.
# """

# from __future__ import annotations

# import argparse
# import os
# import random
# import sys
# import time
# from dataclasses import dataclass
# from pathlib import Path

# # --- Setup path ---
# ROOT = Path(__file__).resolve().parents[1]
# if str(ROOT) not in sys.path:
#     sys.path.insert(0, str(ROOT))

# # --- Load env ---
# try:
#     from dotenv import load_dotenv

#     for env_file in (ROOT / "environment.env", ROOT / ".env"):
#         if env_file.is_file():
#             load_dotenv(env_file)
#             break
# except ImportError:
#     pass

# if not os.getenv("DATABASE_HOST_URL"):
#     print("ERROR: DATABASE_HOST_URL not set.")
#     raise SystemExit(1)

# # --- Imports ---
# from app.database.session import SessionLocal
# from app.listener import check_area_occupancy
# from app.models.area import Area


# # --- Data class ---
# @dataclass(frozen=True)
# class AreaTarget:
#     area_id: int
#     processor_id: int
#     area_code: int
#     name: str | None


# # --- Helpers ---
# def _parse_int_code(v) -> int | None:
#     try:
#         return int(str(v).strip())
#     except Exception:
#         return None


# def _build_area_status_payload(
#     *,
#     area_code: int,
#     power_w: float,
#     max_power_w: float,
#     occupancy: str,
#     scene_id: int,
# ) -> dict:
#     return {
#         "AreaStatuses": [
#             {
#                 "href": f"/area/{area_code}/status",
#                 "OccupancyStatus": occupancy,
#                 "CurrentScene": {"href": f"/scene/{scene_id}"},
#                 "InstantaneousPower": power_w,
#                 "InstantaneousMaxPower": max_power_w,
#             }
#         ]
#     }


# def _load_targets(db) -> list[AreaTarget]:
#     areas = db.query(Area).order_by(Area.id.asc()).all()

#     targets = []
#     skipped = 0

#     for a in areas:
#         code = _parse_int_code(a.code)

#         if code is None or a.processor_id is None:
#             skipped += 1
#             continue

#         targets.append(
#             AreaTarget(
#                 area_id=int(a.id),
#                 processor_id=int(a.processor_id),
#                 area_code=int(code),
#                 name=getattr(a, "name", None),
#             )
#         )

#     if not targets:
#         raise SystemExit("No valid areas found.")

#     print(f"Loaded {len(targets)} areas (skipped {skipped})")
#     return targets


# # --- Main injector ---
# def run_injector(
#     *,
#     min_interval: float,
#     max_interval: float,
#     max_power_w: float,
#     occupied_prob: float,
#     scene_min: int,
#     scene_max: int,
#     dry_run: bool,
#     max_events: int | None,
# ):
#     db = SessionLocal()

#     try:
#         targets = _load_targets(db)

#         next_fire = {
#             t: time.monotonic() + random.uniform(min_interval, max_interval)
#             for t in targets
#         }

#         injected = 0

#         print("🚀 Starting injector (Ctrl+C to stop)")

#         while True:
#             now = time.monotonic()

#             for t, ts in list(next_fire.items()):
#                 if ts > now:
#                     continue

#                 if max_events and injected >= max_events:
#                     print("Done.")
#                     return

#                 # Reschedule
#                 next_fire[t] = now + random.uniform(min_interval, max_interval)

#                 # --- Random occupancy ---
#                 occupancy = "Occupied" if random.random() < occupied_prob else "Unoccupied"

#                 # --- Scene ---
#                 scene_id = random.randint(scene_min, scene_max)

#                 # --- Power logic (REALISTIC) ---
#                 if occupancy == "Occupied":
#                     power = round(random.uniform(10, max_power_w), 2)
#                 else:
#                     power = round(random.uniform(0, 5), 2)

#                 max_power = float(max_power_w)

#                 payload = _build_area_status_payload(
#                     area_code=t.area_code,
#                     power_w=power,
#                     max_power_w=max_power,
#                     occupancy=occupancy,
#                     scene_id=scene_id,
#                 )

#                 label = f"[area {t.area_id} | code {t.area_code}]"

#                 if dry_run:
#                     print(f"[dry] {label} occ={occupancy} power={power}")
#                     injected += 1
#                     continue

#                 try:
#                     check_area_occupancy(payload, db, t.processor_id)
#                     injected += 1
#                     print(f"{injected}: {label} occ={occupancy} power={power}")
#                 except Exception as e:
#                     db.rollback()
#                     print(f"ERROR: {e}")

#             time.sleep(0.2)

#     except KeyboardInterrupt:
#         print("\nStopped.")

#     finally:
#         db.close()


# # --- CLI ---
# def main():
#     p = argparse.ArgumentParser()

#     p.add_argument("--min-interval", type=float, default=1)
#     p.add_argument("--max-interval", type=float, default=5)
#     p.add_argument("--max-power", type=float, default=100)
#     p.add_argument("--occupied-prob", type=float, default=0.7)
#     p.add_argument("--scene-min", type=int, default=1)
#     p.add_argument("--scene-max", type=int, default=4)
#     p.add_argument("--dry-run", action="store_true")
#     p.add_argument("--max-events", type=int, default=None)

#     args = p.parse_args()

#     run_injector(
#         min_interval=args.min_interval,
#         max_interval=args.max_interval,
#         max_power_w=args.max_power,
#         occupied_prob=args.occupied_prob,
#         scene_min=args.scene_min,
#         scene_max=args.scene_max,
#         dry_run=args.dry_run,
#         max_events=args.max_events,
#     )


# if __name__ == "__main__":
#     main()
"""
Continuously inject fake listener AreaStatuses for ALL areas in the DB.

This script is meant for load/demo testing of listener ingestion paths.
It scans all areas, then periodically calls `app.listener.check_area_occupancy`
with synthetic `{"AreaStatuses": [...]}` payloads at per-area random intervals.

Stop with Ctrl+C.

Examples (run from repo root):

  python scripts/demo_inject_all_areas_listener.py
  python scripts/demo_inject_all_areas_listener.py --min-interval 1 --max-interval 5
  python scripts/demo_inject_all_areas_listener.py --dry-run --max-events 10
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    for env_file in (ROOT / "environment.env", ROOT / ".env"):
        if env_file.is_file():
            load_dotenv(env_file)
            break
except ImportError:
    pass

if not os.getenv("DATABASE_HOST_URL"):
    print("ERROR: DATABASE_HOST_URL not set (environment.env or .env).")
    raise SystemExit(1)


from app.database.session import SessionLocal
from app.listener import check_area_occupancy
from app.models.area import Area


@dataclass(frozen=True)
class AreaTarget:
    area_id: int
    processor_id: int
    area_code: int
    name: str | None


def _parse_int_code(v) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _build_area_status_payload(
    *,
    area_code: int,
    include_power: bool,
    power_w: float | None,
    max_power_w: float | None,
    occupancy: str,
    scene_id: int,
) -> dict:
    status: dict = {
        "href": f"/area/{area_code}/status",
        "OccupancyStatus": occupancy,
        "CurrentScene": {"href": f"/scene/{scene_id}"},
    }
    if include_power:
        status["InstantaneousPower"] = power_w
        status["InstantaneousMaxPower"] = max_power_w
    return {"AreaStatuses": [status]}


def _load_targets(db, only_area_ids: set[int] | None, only_processor_id: int | None) -> list[AreaTarget]:
    q = db.query(Area).order_by(Area.id.asc())
    if only_processor_id is not None:
        q = q.filter(Area.processor_id == only_processor_id)
    if only_area_ids:
        q = q.filter(Area.id.in_(sorted(only_area_ids)))

    areas = q.all()
    targets: list[AreaTarget] = []
    skipped = 0
    for a in areas:
        code = _parse_int_code(a.code)
        if code is None or a.processor_id is None:
            skipped += 1
            continue
        targets.append(
            AreaTarget(
                area_id=int(a.id),
                processor_id=int(a.processor_id),
                area_code=int(code),
                name=getattr(a, "name", None),
            )
        )
    if not targets:
        raise SystemExit("No injectable areas found (need numeric Area.code and non-null processor_id).")
    print(f"Loaded {len(targets)} injectable area(s) (skipped {skipped}).")
    return targets


def run_injector(
    *,
    min_interval_sec: float,
    max_interval_sec: float,
    power_min_w: float,
    power_max_w: float,
    max_power_w: float,
    occupied_prob: float,
    include_power: bool,
    scene_min: int,
    scene_max: int,
    dry_run: bool,
    max_events: int | None,
    only_area_ids: set[int] | None,
    only_processor_id: int | None,
) -> None:
    if min_interval_sec <= 0 or max_interval_sec <= 0 or max_interval_sec < min_interval_sec:
        raise SystemExit("Invalid interval settings: require 0 < min_interval <= max_interval.")
    if power_max_w < power_min_w:
        raise SystemExit("Invalid power range: require power-min <= power-max.")
    if not (0.0 <= occupied_prob <= 1.0):
        raise SystemExit("Invalid --occupied-prob: must be within [0, 1].")
    if scene_min <= 0 or scene_max < scene_min:
        raise SystemExit("Invalid scene range: require scene-min >= 1 and scene-min <= scene-max.")

    db = SessionLocal()
    try:
        targets = _load_targets(db, only_area_ids=only_area_ids, only_processor_id=only_processor_id)
        now = time.monotonic()
        next_fire: dict[AreaTarget, float] = {
            t: now + random.uniform(min_interval_sec, max_interval_sec) for t in targets
        }

        injected = 0
        print(
            "Starting injector. Press Ctrl+C to stop.\n"
            f"  areas={len(targets)}  interval=[{min_interval_sec},{max_interval_sec}]s"
            f"  include_power={include_power}  dry_run={dry_run}"
            + (f"  max_events={max_events}" if max_events is not None else "")
        )

        while True:
            now = time.monotonic()
            due = [t for t, ts in next_fire.items() if ts <= now]

            if not due:
                # Sleep until the nearest scheduled injection (capped for responsiveness to Ctrl+C).
                sleep_for = max(0.0, min(next_fire.values()) - now)
                time.sleep(min(0.5, sleep_for))
                continue

            for t in due:
                if max_events is not None and injected >= max_events:
                    print(f"Reached max events ({max_events}); exiting.")
                    return

                # Reschedule first to avoid rapid retries if injection raises.
                next_fire[t] = time.monotonic() + random.uniform(min_interval_sec, max_interval_sec)

                occupancy = "Occupied" if random.random() < occupied_prob else "Unoccupied"
                scene_id = random.randint(scene_min, scene_max)

                power = None
                mp = None
                if include_power:
                    mp = float(max_power_w)
                    power = float(random.uniform(power_min_w, min(power_max_w, mp)))

                payload = _build_area_status_payload(
                    area_code=t.area_code,
                    include_power=include_power,
                    power_w=power,
                    max_power_w=mp,
                    occupancy=occupancy,
                    scene_id=scene_id,
                )

                label = f"area_id={t.area_id} code={t.area_code} P{t.processor_id}"
                if t.name:
                    label += f" name={t.name!r}"

                if dry_run:
                    injected += 1
                    print(f"[dry-run] inject #{injected}: {label} occ={occupancy} scene={scene_id} power={power} max={mp}")
                    continue

                try:
                    check_area_occupancy(payload, db, t.processor_id)
                    injected += 1
                    print(f"inject #{injected}: {label} occ={occupancy} scene={scene_id} power={power} max={mp}")
                except Exception as e:
                    db.rollback()
                    print(f"ERROR injecting {label}: {e!r}")

    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C).")
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Inject fake listener AreaStatuses for all areas until Ctrl+C.")
    p.add_argument("--min-interval", type=float, default=1.0, help="Minimum seconds between injections per area.")
    p.add_argument("--max-interval", type=float, default=5.0, help="Maximum seconds between injections per area.")
    p.add_argument("--power-min", type=float, default=0.0, help="Minimum InstantaneousPower (W).")
    p.add_argument("--power-max", type=float, default=60.0, help="Maximum InstantaneousPower (W).")
    p.add_argument("--max-power", type=float, default=100.0, help="InstantaneousMaxPower (W).")
    p.add_argument("--occupied-prob", type=float, default=0.7, help="Probability of Occupied vs Unoccupied.")
    p.add_argument(
        "--no-power",
        action="store_true",
        help="Do not include power fields (skips energy-related handling in listener).",
    )
    p.add_argument("--scene-min", type=int, default=1, help="Minimum scene id for CurrentScene.href (/scene/<id>).")
    p.add_argument("--scene-max", type=int, default=4, help="Maximum scene id for CurrentScene.href (/scene/<id>).")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call listener; just print what would be injected.",
    )
    p.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Exit after injecting this many events (useful for testing). Default: run forever.",
    )
    p.add_argument(
        "--only-processor-id",
        type=int,
        default=None,
        help="Optional filter: only inject for areas belonging to this processor_id.",
    )
    p.add_argument(
        "--only-area-ids",
        type=str,
        default=None,
        help="Optional filter: comma-separated Area.id list, e.g. '1,2,5'.",
    )
    args = p.parse_args()

    only_area_ids: set[int] | None = None
    if args.only_area_ids:
        try:
            only_area_ids = {int(x.strip()) for x in args.only_area_ids.split(",") if x.strip()}
        except ValueError:
            raise SystemExit("--only-area-ids must be a comma-separated list of integers.")

    run_injector(
        min_interval_sec=float(args.min_interval),
        max_interval_sec=float(args.max_interval),
        power_min_w=float(args.power_min),
        power_max_w=float(args.power_max),
        max_power_w=float(args.max_power),
        occupied_prob=float(args.occupied_prob),
        include_power=not bool(args.no_power),
        scene_min=int(args.scene_min),
        scene_max=int(args.scene_max),
        dry_run=bool(args.dry_run),
        max_events=int(args.max_events) if args.max_events is not None else None,
        only_area_ids=only_area_ids,
        only_processor_id=int(args.only_processor_id) if args.only_processor_id is not None else None,
    )


if __name__ == "__main__":
    main()


