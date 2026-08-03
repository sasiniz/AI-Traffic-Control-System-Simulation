"""
Offline generator that expands an hour-by-hour green-time plan into a full
phase-by-phase signal timeline CSV.

This is a stand-in for what the Random Forest scheduler will eventually
produce. traffic_sim.py never runs this logic at run time and never will -
it is meant to read the finished CSV once that hookup is built. Running
this script only ever writes a data file; it has no effect on the live
simulation, and the "signals never react to incidents" rule is untouched.

Usage:
    python generate_timeline.py
"""

import csv

AMBER_SECONDS = 3
HOUR_SECONDS = 3600
MIN_GREEN_TO_START = 12          # a phase shorter than this never begins
ROTATION = ["North", "South", "East", "West"]

# Placeholder plan: hour of day -> green seconds per road. Swap this dict
# for the real model output later - nothing else in this file needs to
# change.
PLAN = {
    7: {"North": 51, "South": 18, "East": 13, "West": 12},
    8: {"North": 51, "South": 17, "East": 14, "West": 12},
    9: {"North": 51, "South": 15, "East": 15, "West": 12},
}


def compile_timeline(plan, start_road, date, output_path):
    """
    Expand an hour -> {road: green_seconds} plan into one row per phase and
    write it to `output_path`. Returns the list of row dicts written.

    Rotation is continuous across hours: only the very first phase of the
    whole file uses `start_road`; every hour after that picks up wherever
    the previous hour left off, and only the green durations change at the
    boundary, from the new hour's plan.

    Each hour must sum to exactly 3600 seconds of green + amber. When the
    next road's full phase will not fit in what is left, a one-phase
    lookahead decides how to close the hour out exactly:
      - if the next road could still get 12s or more of green, it starts
        with that shortened green and the hour ends there;
      - otherwise it does not start at all - the current road's green is
        extended to fill the hour exactly, and the skipped road becomes
        the first phase of the next hour instead.
    """
    rows = []
    road = start_road
    phase_index = 0

    for hour in sorted(plan):
        hour_plan = plan[hour]
        remaining = HOUR_SECONDS

        while remaining > 0:
            green = hour_plan[road]
            next_road = ROTATION[(ROTATION.index(road) + 1) % len(ROTATION)]
            after_this = remaining - (green + AMBER_SECONDS)
            next_cost = hour_plan[next_road] + AMBER_SECONDS

            if next_cost <= after_this:
                # The next phase fits too - commit this one at full length
                # and carry straight on within the same hour.
                rows.append(_row(date, hour, phase_index, road, green))
                phase_index += 1
                remaining = after_this
                road = next_road
                continue

            possible_green = after_this - AMBER_SECONDS

            if possible_green >= MIN_GREEN_TO_START:
                rows.append(_row(date, hour, phase_index, road, green))
                phase_index += 1
                rows.append(_row(date, hour, phase_index, next_road, possible_green))
                phase_index += 1
                road = ROTATION[(ROTATION.index(next_road) + 1) % len(ROTATION)]
            else:
                extended_green = remaining - AMBER_SECONDS
                rows.append(_row(date, hour, phase_index, road, extended_green))
                phase_index += 1
                road = next_road

            remaining = 0

    write_csv(rows, output_path)
    return rows


def _row(date, hour, phase_index, road, green_seconds):
    return {
        "date": date,
        "start_hour": hour,
        "phase_index": phase_index,
        "road": road,
        "green_seconds": green_seconds,
        "amber_seconds": AMBER_SECONDS,
    }


def write_csv(rows, path):
    fields = ["date", "start_hour", "phase_index", "road", "green_seconds", "amber_seconds"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    rows = compile_timeline(PLAN, start_road="North", date="2026-06-15",
                             output_path="signal_timeline_sample.csv")
    print(f"Wrote {len(rows)} phase rows to signal_timeline_sample.csv")
