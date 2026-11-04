from __future__ import annotations

import argparse
import json

from app.processing.stage_runner import STAGES, run_processing_stage


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute one persisted Clipforge processing stage.")
    parser.add_argument("job_id", help="Existing processing job ID")
    parser.add_argument("stage", choices=STAGES, help="Stage to run; earlier stage artifacts must exist")
    args = parser.parse_args()
    result = run_processing_stage(args.job_id, args.stage)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
