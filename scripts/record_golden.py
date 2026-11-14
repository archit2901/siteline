"""Save a finished live run as a golden run for offline replay.

  PYTHONPATH=. python scripts/record_golden.py <run_id> <name>

Copies runs/<run_id>/events.jsonl and its artifacts to data/golden/<name>.jsonl and data/golden/<name>/ (GOLDEN_DIR
overrides). Golden runs hold real CoStar spaces, so they stay under data/ and are never committed. Refuses runs that
did not end with a done event or whose report is not verified. Replay: POST /runs {mode: "replay", golden: <name>}.
"""

import argparse
import shutil
import sys

from scripts.e2e import ARTIFACTS
from services.agent.llm import runs_dir
from services.agent.runs import GOLDEN, GOLDEN_NAME, load_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("name")
    args = parser.parse_args()
    if not GOLDEN_NAME.match(args.name):
        sys.exit("name must match [A-Za-z0-9_-]{1,64}")
    src = runs_dir() / args.run_id
    events = load_events(src / "events.jsonl")
    if not events or events[-1].type != "done":
        sys.exit(f"run {args.run_id} did not finish (last event: {events[-1].type if events else 'none'})")
    files = {e.payload.get("name") for e in events if e.type == "file"} & ARTIFACTS.keys()
    for name in sorted(files):
        data = ARTIFACTS[name].validate_json((src / name).read_bytes())
        if name == "report.json" and not data.verified:
            sys.exit("report.json is not verified; record a run whose claims all check out")

    dest = GOLDEN / args.name
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "events.jsonl", GOLDEN / f"{args.name}.jsonl")
    for name in sorted(files):
        shutil.copyfile(src / name, dest / name)
    print(f"golden {args.name}: {len(events)} events, artifacts {sorted(files)} -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
