# Podcast queue

Handoff directory between the two *producers* of curated episodes and the
single *consumer* that turns them into audio.

Producers (either may commit a file here; they don't coordinate):

1. **Local deep-research runner** — `scripts/podcast_research_run.sh`
   (`podcast-research.timer`, 03:47 daily). Picks the next due topic with
   `scripts/podcast_next_topic.py`, runs the `podcast-deep-research`
   workflow (four domain-specific research lenses → adversarial per-source
   verification → editorial curation), and writes the file via
   `scripts/podcast_queue_writer.py`. Covers every domain, medical and not.
2. **Cloud routine** — `scripts/podcast_researcher_agent.md`, medicine only.

Consumer: the local orchestrator `scripts/podcast_researcher.py`
(`podcast-pipeline.timer`, 06:15 daily) on arch-vps.

Each file is `<topic-id>-<YYYY-MM-DD>.json`, shaped as:

```json
{
  "topic_id": "ed-sepsis-bundle-controversies",
  "title": "ED Sepsis Bundle Controversies — 2026-08-20",
  "audio_format": "debate",
  "sources": [
    {"url": "https://...", "title": "...", "why": "one line: why this source, what's new"}
  ],
  "rationale": "one paragraph: what's new, why these sources, why this format",
  "case_vignette": "OPTIONAL: 2-3 sentence opening case/scenario for the hosts to work through",
  "style": "OPTIONAL: full host-style instructions overriding the default Deranged Physiology framing"
}
```

`case_vignette` and `style` are optional and come from the deep-research
workflow rather than the cloud routine. When present, `case_vignette` is
appended to the audio instructions so the episode opens with a concrete
case/scenario, and `style` replaces the default Deranged Physiology host framing —
that's what lets a motorcycling or photography episode sound like its own
show instead of a medical one.

Files here double as the deep-research runner's *history*: it picks the
least-recently-covered topic by reading these filenames, and gathers the
URLs to avoid from these payloads. That's why nothing deletes or rewrites
them — removing a file makes its topic look uncovered and lets already-used
sources resurface.

The orchestrator consumes files oldest-first (one per run, so a full queue
paces out over days) and tracks what it has already processed in
`~/.notebooklm/podcast_pipeline_state.json` (local-only, not committed).
Nothing in this directory should be edited by hand except for debugging.
