
from __future__ import annotations

import argparse
import os
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# --- PATH SETUP ---
APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --- ENV LOAD ---
try:
    from dotenv import load_dotenv

    for env_file in (
        REPO_ROOT / "environment.env",
        REPO_ROOT / ".env",
        APP_ROOT / "environment.env",
        APP_ROOT / ".env",
    ):
        if env_file.is_file():
            load_dotenv(env_file)
            break
except ImportError:
    pass

if not os.getenv("DATABASE_HOST_URL"):
    print("ERROR: DATABASE_HOST_URL not set.")
    sys.exit(1)

# --- IMPORTS ---
from app.database.session import SessionLocal
from app.models.area import Area
from app.listener import check_area_occupancy

# --- CONFIG ---
BATCH_SIZE = 500
MAX_WORKERS = 10

# possible occupancy states
OCCUPANCY_STATES = ["Occupied", "Unoccupied"]


# --- HELPERS ---
def build_msg(area_code: int, power: float, max_power: float):
    occupancy = random.choice(OCCUPANCY_STATES)

    return {
        "AreaStatuses": [
            {
                "href": f"/area/{area_code}/status",
                "InstantaneousPower": power,
                "InstantaneousMaxPower": max_power,
                "OccupancyStatus": occupancy,
                "CurrentScene": {"href": "/scene/1"},
            }
        ]
    }


def parse_area_ids(area_ids_str: str | None):
    if not area_ids_str:
        return []
    return [int(x.strip()) for x in area_ids_str.split(",") if x.strip()]


def load_area_ids_from_file(file_path: str | None):
    if not file_path:
        return []

    ids = []
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            for p in parts:
                p = p.strip()
                if p:
                    ids.append(int(p))

    return ids


# --- PROCESS SINGLE AREA ---
def validate_area_ids(area_ids):
    db = SessionLocal()
    try:
        existing_ids = set(
            r[0] for r in db.query(Area.id).filter(Area.id.in_(area_ids)).all()
        )

        valid = []
        invalid = []

        for aid in area_ids:
            if aid in existing_ids:
                valid.append(aid)
            else:
                invalid.append(aid)

        if invalid:
            print(f"WARNING: {len(invalid)} invalid area IDs skipped: {invalid[:10]}...")

        print(f"Valid area IDs: {len(valid)} / {len(area_ids)}")
        return valid

    finally:
        db.close()


def process_area(area_id: int):
    db = SessionLocal()
    try:
        area = db.query(Area).filter(Area.id == area_id).first()
        if not area or area.processor_id is None:
            return

        try:
            area_code = int(str(area.code).strip())
        except:
            return

        # randomize power slightly
        power = random.uniform(20.0, 80.0)

        msg = build_msg(area_code, power, 100.0)

        check_area_occupancy(msg, db, area.processor_id)

    finally:
        db.close()


# --- PROCESS BATCH ---
def process_batch(area_ids):
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_area, aid) for aid in area_ids]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print("Error:", e)


# --- MAIN LOOP ---
def run(area_ids: list[int], duration: int, interval: int):
    end_time = time.time() + duration
    tick = 0

    while time.time() < end_time:
        tick += 1
        print(f"\n--- Tick {tick} ---")

        batch = []
        for aid in area_ids:
            batch.append(aid)

            if len(batch) >= BATCH_SIZE:
                process_batch(batch)
                batch = []

        if batch:
            process_batch(batch)

        print(f"Tick {tick} complete (processed {len(area_ids)} areas)")
        time.sleep(interval)


# --- CLI ---
def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--area-ids", type=str, help="Comma-separated IDs (e.g. 1,2,3)")
    parser.add_argument(
        "--area-file",
        type=str,
        default=str(REPO_ROOT / "app" / "scripts" / "area_ids.txt"),
        help="Path to file with area IDs",
    )

    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--interval", type=int, default=10)

    args = parser.parse_args()

    # --- LOAD IDS ---
    area_ids = []

    area_ids.extend(parse_area_ids(args.area_ids))
    area_ids.extend(load_area_ids_from_file(args.area_file))

    area_ids = list(set(area_ids))

    if not area_ids:
        print("ERROR: No area IDs provided")
        return

    print(f"Loaded {len(area_ids)} area IDs")

    # validate against DB
    area_ids = validate_area_ids(area_ids)

    if not area_ids:
        print("ERROR: No valid area IDs found in database")
        return

    run(area_ids, args.duration, args.interval)


if __name__ == "__main__":
    main()
