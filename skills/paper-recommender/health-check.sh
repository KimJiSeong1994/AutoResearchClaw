#!/usr/bin/env bash
# Health-check for the daily-research pipeline. Reads last_run_status.json
# on EC2 and warns about drift. Run this manually weekly, or wire into a
# laptop cron / launchd job for daily local checks.
#
# Exit code 0 = healthy, 1 = warnings present.
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_STATUS="${REMOTE_STATUS:-~/.openclaw/workspace/projects/paper-recommender/state/last_run_status.json}"

# Pull the status file content via SSH; survives if jq is missing locally.
status_json="$(ssh -i "$KEY_FILE" "$REMOTE_HOST" "cat $REMOTE_STATUS 2>/dev/null || true")"

if [ -z "$status_json" ]; then
  echo "FAIL: no last_run_status.json on EC2 — pipeline never ran (or wrong path)"
  exit 1
fi

# Use python3 (always available) instead of jq for portability.
python3 - <<PY
import json, sys, datetime as dt

s = json.loads('''$status_json''')
warnings = []

ts = s.get("timestamp")
if ts:
    try:
        last = dt.datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        age_h = (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 3600
        if age_h > 25:
            warnings.append(f"stale: last run was {age_h:.1f}h ago (>25h)")
    except ValueError:
        warnings.append(f"unparseable timestamp: {ts!r}")

if s.get("dry_run"):
    warnings.append("last run was a dry-run — no real artifacts produced")

if (s.get("candidate_count") or 0) < 5:
    warnings.append(f"low candidates: {s.get('candidate_count')} (<5)")

if (s.get("seed_topic_count") or 0) < 2:
    warnings.append(f"low seed topics: {s.get('seed_topic_count')} (<2)")

if (s.get("deep_attempted") or 0) > 0 and (s.get("deep_success_count") or 0) == 0:
    warnings.append("zero successful deep runs out of "
                    f"{s.get('deep_attempted')} attempted")

if s.get("used_fallback"):
    warnings.append("embedding fallback was active (no semantic clustering)")

print(f"timestamp:  {ts}")
print(f"candidates: {s.get('candidate_count')}")
print(f"clusters:   {s.get('cluster_count')}")
print(f"deep ok:    {s.get('deep_success_count')}/{s.get('deep_attempted')}")
print(f"fallback:   {s.get('used_fallback')}")
print(f"sources:    {s.get('source_stats')}")
print(f"wall:       {s.get('wall_clock_sec'):.0f}s" if s.get('wall_clock_sec') is not None else "wall: ?")
print(f"seeds:      {s.get('seed_topic_count')}")
print()

if warnings:
    print("WARNINGS:")
    for w in warnings:
        print(f"  - {w}")
    sys.exit(1)
else:
    print("OK: pipeline healthy")
PY
