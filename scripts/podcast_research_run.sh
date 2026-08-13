#!/usr/bin/env bash
# Scheduled deep-research half of the podcast pipeline.
#
# Picks the next due topic across every domain, runs the podcast-deep-research
# workflow (four research lenses -> adversarial source verification ->
# editorial curation) in a headless Claude Code session, and commits the
# resulting queue file. The orchestrator (podcast-pipeline.timer) picks it up
# on its next run and turns it into a downloaded mp3.
#
# Trust boundary
# --------------
# The research agents ingest arbitrary web pages, so everything the session
# returns is untrusted input. The session therefore does exactly one thing:
# run the workflow and print the resulting JSON payload on stdout. It is given
# no file-writing tool, no git access, and no general shell — only the
# research tools and a read-only curl status check. This script, which is
# trusted, does all the consequential work: validate the JSON, re-check every
# source URL itself, write the queue file, and commit.
#
# (Path-scoped Write/Edit allowlist rules are NOT enforced for headless
# sessions — verified empirically — so "let the session write only the queue
# file" is not a control that actually holds. Hence this split.)
#
# Exits 0 when nothing is due — every topic covered within its cooldown.

set -euo pipefail

REPO="/home/luka/projects/notebooklm-py"
NTFY_URL="http://localhost:2586/agent"
LOG=/tmp/podcast-research-last.log
MIN_SOURCES=4

cd "$REPO"

notify() {
  curl -sS -X POST "$NTFY_URL" -H "Title: $1" -d "$2" >/dev/null 2>&1 || true
  echo "$2"
}

# Never research on top of uncommitted work — this script commits and pushes,
# and sweeping up someone else's in-progress changes would be worse than
# skipping a cycle.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree dirty, skipping this cycle" >&2
  exit 0
fi

git fetch --quiet origin main
git merge --quiet --ff-only origin/main

if ! ARGS=$(uv run python scripts/podcast_next_topic.py 2>/dev/null) || [[ -z "$ARGS" ]]; then
  echo "nothing due" >&2
  exit 0
fi

TOPIC_ID=$(printf '%s' "$ARGS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["topic"]["id"])')
DATE=$(printf '%s' "$ARGS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["date"])')
QUEUE_FILE="scripts/podcast_queue/${TOPIC_ID}-${DATE}.json"

echo "researching ${TOPIC_ID} for ${DATE}"

PROMPT=$(
  cat <<EOF
Call the Workflow tool exactly once with:
  scriptPath: "${REPO}/.claude/workflows/podcast-deep-research.js"
  args: ${ARGS}

When it returns, print the workflow result's \`payload\` object as raw JSON and
nothing else — no prose, no explanation, no markdown fences, before or after.
Your entire final message must be that JSON object and must parse as JSON.

Do not create, edit, or delete any file. Do not run any git command.
EOF
)

# Everything the run legitimately needs and nothing else. Notably absent:
# any file-editing tool, any git command, and any general-purpose interpreter
# (python3, uv run, sh) — each of which would turn a prompt injection buried
# in a fetched page into arbitrary code execution on this host. The single
# permitted Bash form pins its output to /dev/null so the verification agents
# can check HTTP status without being able to write anything.
ALLOWED_TOOLS=(
  Workflow Read ToolSearch WebFetch TodoWrite
  "Bash(curl -sL -o /dev/null -w:*)"
  "mcp__claude_ai_Exa__*" "mcp__claude_ai_Semantic_Scholar__*"
  "mcp__claude_ai_Consensus__*" "mcp__claude_ai_Stealth_Scraper__*"
)

if ! claude -p "$PROMPT" --allowedTools "${ALLOWED_TOOLS[@]}" >"$LOG" 2>&1; then
  notify "Podcast research failed: ${TOPIC_ID}" \
    "Research session exited non-zero for ${TOPIC_ID} (${DATE}). See ${LOG}."
  exit 1
fi

# The session's stdout is untrusted: parse it defensively, keep only the
# fields the queue format defines, and re-verify every URL here rather than
# trusting the agent's own verification.
if ! SUMMARY=$(QUEUE_FILE="$QUEUE_FILE" TOPIC_ID="$TOPIC_ID" DATE="$DATE" \
  MIN_SOURCES="$MIN_SOURCES" python3 scripts/podcast_queue_writer.py <"$LOG"); then
  notify "Podcast research failed: ${TOPIC_ID}" \
    "Could not build a valid queue file for ${TOPIC_ID} (${DATE}). See ${LOG}."
  exit 1
fi

git add -- "$QUEUE_FILE"
git commit --quiet -m "chore(podcast): queue ${TOPIC_ID} — ${DATE}"
if ! git push --quiet origin main 2>/dev/null; then
  git fetch --quiet origin main
  git rebase --quiet origin/main
  git push --quiet origin main
fi

notify "Podcast queued: ${TOPIC_ID}" "Queued ${TOPIC_ID} (${DATE})
${SUMMARY}"
