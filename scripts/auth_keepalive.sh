#!/usr/bin/env bash
# NotebookLM auth keepalive — scheduled one-shot cookie-jar poke.
#
# Why: a fresh interactive `notebooklm login` on this host lasted under 24h
# (succeeded 2026-09-12 09:40, dead by the 06:15 pipeline run next morning), so
# `podcast-pipeline.service` dies silently at 06:15 and takes the CLI, REST API
# (:8004) and MCP server (:8005) down with it. The library documents a 15-20 min
# keepalive cadence; this is that timer.
#
# Contract: ALWAYS exit 0. A dead session is surfaced by podcast-pipeline.service
# failing at 06:15 (the real alarm); this unit must not add a permanently-failed
# unit to `systemctl --user list-units --state=failed`, which is the heartbeat's
# DOWN signal. Outcome is logged for inspection instead.
#
# Latency: the 06:15 pipeline alarm only fires once a day, so a session that dies
# at 07:08 stays invisible for ~23h (observed 2026-09-14). After 3 consecutive
# refresh failures this script now pushes an ops-alert (max one per 24h) so the
# interactive `notebooklm login` happens the same day. Exit code stays 0.
set -uo pipefail

NLM=/home/luka/projects/notebooklm-py/.venv/bin/notebooklm
LOG=/home/luka/logs/notebooklm-auth-keepalive.log
STATE=/home/luka/.notebooklm/keepalive_state
ALERT=/home/luka/.local/bin/hermes-alert
mkdir -p "$(dirname "$LOG")"

ts() { date '+%Y-%m-%d %H:%M:%S%z'; }

out=$("$NLM" auth refresh 2>&1)
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "$(ts) OK   keepalive refresh succeeded" >>"$LOG"
else
  # Collapse consecutive identical failures so the log stays readable.
  printf '%s FAIL rc=%s %s\n' "$(ts)" "$rc" "$(printf '%s' "$out" | grep -iE 'Authentication expired|Run .notebooklm login.|Error' | head -2 | paste -sd' | ' -)" >>"$LOG"
fi

# --- consecutive-failure counter + ops-alert (additive; exit-0 contract kept) ---
cfails=0
last_alert=0
if [ -f "$STATE" ]; then
  # shellcheck disable=SC1090
  . "$STATE" 2>/dev/null
else
  # Bootstrap from the log (trailing FAIL run) so a lost state file cannot
  # restart the counter at 1 and re-introduce the multi-hour blind spot.
  cfails=$(tac "$LOG" 2>/dev/null | awk '{if (/ FAIL /) c++; else exit} END{print c+0}')
fi

if [ "$rc" -eq 0 ]; then
  cfails=0
  last_alert=0
else
  cfails=$((cfails + 1))
fi

if [ "$cfails" -ge 3 ]; then
  now=$(date +%s)
  if [ $((now - last_alert)) -ge 86400 ]; then
    msg="NotebookLM auth dead on arch-vps: $cfails consecutive keepalive failures. Needs interactive 'notebooklm login'; until then podcast-pipeline ($LOG) fails daily at 06:15."
    if "$ALERT" --route ops-alert --source notebooklm-auth-keepalive --event alert --message "$msg" >>"$LOG" 2>&1; then
      echo "$(ts) ALERT sent after $cfails consecutive failures" >>"$LOG"
      last_alert=$now
    else
      echo "$(ts) ALERT failed to send rc=$?" >>"$LOG"
    fi
  fi
fi
printf 'cfails=%s\nlast_alert=%s\n' "$cfails" "$last_alert" >"$STATE" 2>/dev/null || true

# Keep the log bounded.
if [ "$(wc -l <"$LOG" 2>/dev/null || echo 0)" -gt 2000 ]; then
  tail -n 500 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exit 0
