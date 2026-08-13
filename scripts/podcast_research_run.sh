#!/usr/bin/env bash
# Scheduled deep-research half of the podcast pipeline.
#
# Picks the next due topic across every domain, runs the podcast-deep-research
# workflow (four research lenses -> adversarial source verification ->
# editorial curation) in a headless Claude Code session, and commits the
# resulting queue file. The orchestrator (podcast-pipeline.timer) picks it up
# on its next run and turns it into a downloaded mp3.
#
# Exits 0 when nothing is due — every topic covered within its cooldown.

set -euo pipefail

REPO="/home/luka/projects/notebooklm-py"
NTFY_URL="http://localhost:2586/agent"

cd "$REPO"

# Never research on top of uncommitted work — the session commits and pushes,
# and sweeping up someone else's in-progress changes would be worse than
# skipping a cycle.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree dirty, skipping this cycle" >&2
  exit 0
fi

git fetch --quiet origin main
git merge --quiet --ff-only origin/main

if ! ARGS=$(uv run python scripts/podcast_next_topic.py 2>/dev/null); then
  echo "topic selection failed" >&2
  exit 1
fi

if [[ -z "$ARGS" ]]; then
  echo "nothing due" >&2
  exit 0
fi

TOPIC_ID=$(printf '%s' "$ARGS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["topic"]["id"])')
DATE=$(printf '%s' "$ARGS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["date"])')
QUEUE_FILE="scripts/podcast_queue/${TOPIC_ID}-${DATE}.json"

echo "researching ${TOPIC_ID} for ${DATE}"

PROMPT=$(
  cat <<EOF
Run the podcast deep-research workflow and commit its result. Do not ask for
confirmation at any step; this is an unattended scheduled run.

1. Call the Workflow tool with:
   scriptPath: "${REPO}/.claude/workflows/podcast-deep-research.js"
   args: ${ARGS}

2. When it completes, write the workflow's returned \`payload\` object — and
   nothing else from the result — as pretty-printed JSON (2-space indent,
   ensure_ascii false, trailing newline) to:
   ${REPO}/${QUEUE_FILE}

3. Before committing, sanity-check every source URL in the payload with:
   curl -sL -o /dev/null -w "%{http_code}" --max-time 25 "<url>"
   Drop any source that does not return 200. If fewer than 4 sources
   survive, do NOT commit — report the failure and stop.

4. git add that one file only, commit with message
   "chore(podcast): queue ${TOPIC_ID} — ${DATE}", and push to origin main
   (rebase onto origin/main first if the push is rejected).

Touch no other file in the repository. Your final message should be one line:
the topic, the number of sources committed, and the audio format.
EOF
)

if claude -p "$PROMPT" --permission-mode bypassPermissions >/tmp/podcast-research-last.log 2>&1; then
  RESULT=$(tail -5 /tmp/podcast-research-last.log)
else
  RESULT="research session exited non-zero; see /tmp/podcast-research-last.log"
fi

if git -C "$REPO" log --oneline -1 --format=%s | grep -q "queue ${TOPIC_ID} — ${DATE}"; then
  MESSAGE="Queued: ${TOPIC_ID} (${DATE})
${RESULT}"
else
  MESSAGE="Podcast research did NOT queue ${TOPIC_ID} (${DATE})
${RESULT}"
fi

curl -sS -X POST "$NTFY_URL" \
  -H "Title: Podcast research: ${TOPIC_ID}" \
  -d "$MESSAGE" >/dev/null 2>&1 || true

echo "$MESSAGE"
