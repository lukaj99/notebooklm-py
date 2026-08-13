# Podcast queue

Handoff directory between the cloud researcher/curator routine
(`scripts/podcast_researcher_agent.md`) and the local orchestrator
(`scripts/podcast_researcher.py`, run via the `podcast-pipeline.timer`
systemd unit on arch-vps).

The cloud routine commits one file here per curated topic:
`<topic-id>-<YYYY-MM-DD>.json`, shaped as:

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
  "style": "OPTIONAL: full host-style instructions overriding the default EM-Cases framing"
}
```

`case_vignette` and `style` are optional and typically produced by the local
`podcast-deep-research` workflow (`.claude/workflows/podcast-deep-research.js`)
rather than the cloud routine. When present, `case_vignette` is appended to
the audio instructions so the episode opens with a concrete case/scenario,
and `style` replaces the default EM-Cases host framing (used by non-medical
domains — motorcycling, photography, tech — which the workflow researches
with domain-appropriate lenses).

The local orchestrator consumes files here oldest-first, tracks what it's
already processed in `~/.notebooklm/podcast_pipeline_state.json` (not
committed — local-only), and never deletes or rewrites files in this
directory itself. Nothing in this directory should be edited by hand except
for debugging.
