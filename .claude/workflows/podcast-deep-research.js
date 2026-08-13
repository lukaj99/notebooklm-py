export const meta = {
  name: 'podcast-deep-research',
  description: 'Multi-agent deep research + adversarial source verification for one commute-podcast episode (medicine, motorcycling, photography, tech, ...)',
  whenToUse: 'Run on demand (or from a scheduled session) to produce a curated scripts/podcast_queue/ payload for a topic from scripts/podcast_topics.json: four parallel domain-appropriate research lenses, per-source public-accessibility verification, and an editorial curation stage that writes the episode brief, format call, and opening scenario. The caller writes the returned payload to scripts/podcast_queue/<topic-id>-<date>.json and triggers podcast-pipeline.service.',
  phases: [
    { title: 'Research', detail: 'four parallel domain-specific lenses: authority, community/expert commentary, evidence/science, controversy map' },
    { title: 'Verify', detail: 'one adversarial agent per candidate source: public access + content matches claim' },
    { title: 'Curate', detail: 'editorial selection, debate vs deep-dive call, rationale, opening scenario' },
  ],
}

// args: {
//   topic: { id, title, query, debate, domain? },  // one entry from scripts/podcast_topics.json ('query' = pubmed_query for medicine)
//   date: 'YYYY-MM-DD',                            // UTC date (Date.now is unavailable in workflow scripts)
//   avoidUrls: ['...'],                            // URLs already used in prior episodes of this topic
//   maxSources: 6,
// }
if (!args || !args.topic || !args.topic.id || !args.date) {
  throw new Error('args must include topic {id,title,query,debate[,domain]} and date')
}
const topic = args.topic
const date = args.date
const avoid = args.avoidUrls || []
const maxSources = args.maxSources || 6
const domain = topic.domain || 'medicine'

// ---------------------------------------------------------------------------
// Domain profiles: who the listener is, where each research lens looks, how
// the hosts should talk, and what the opening scenario looks like. Medicine
// omits `style` so the orchestrator falls back to its built-in EM-Cases style.
// ---------------------------------------------------------------------------
const DOMAIN_PROFILES = {
  medicine: {
    audience: 'a practicing UK emergency-medicine doctor',
    sourceRules:
      'PubMed abstract pages, open-access journal pages, society guideline pages, and FOAMed posts are ideal. No UpToDate links (paywalled).',
    lenses: {
      authority:
        'GUIDELINES AND SOCIETY STATEMENTS. Find current guidance from relevant professional bodies (ACEP, AHA/ILCOR, EUSEM, RCEM, ACMT/AACT for toxicology, NICE). Use mcp__claude_ai_Exa__web_search_exa or mcp__claude_ai_Exa__deep_search_exa. Flag anything published or updated in the last 2 years, and note where societies disagree with each other.',
      community:
        'FOAMED EXPERT COMMENTARY. Search the high-quality free open-access medical education sites — emcrit.org, rebelem.com, first10em.com, stemlynsblog.org, emergencymedicinecases.com, aliem.com, thepoisonreview.com, litfl.com — for recent expert discussion. Use mcp__claude_ai_Exa__web_search_exa (filter by domain). Only include posts that engage with evidence, not pure opinion.',
      evidence:
        'PRIMARY LITERATURE. Find the strongest recent trials, systematic reviews, and meta-analyses. Prefer mcp__claude_ai_Semantic_Scholar__pubmed_search, mcp__claude_ai_Semantic_Scholar__europe_pmc_search, or mcp__claude_ai_Semantic_Scholar__search_databases. Prioritize RCTs and systematic reviews over case reports. Each "why" line must say what the study actually changes at the bedside.',
      controversy:
        'CONTROVERSY MAP. Identify the 2-4 genuinely contested management questions — places where smart clinicians currently disagree WITH citable evidence on both sides. Use mcp__claude_ai_Consensus__search and/or mcp__claude_ai_Semantic_Scholar__pubmed_search. Where possible return source pairs whose "stance" fields put them on opposite sides. If the evidence has converged, say so — do not manufacture disagreement.',
    },
    curatorPersona:
      'You are the EDITOR of a case-based emergency-medicine podcast in the style of the "Emergency Medicine Cases" podcast.',
    scenarioHint:
      'a realistic, concrete ED case that the chosen sources genuinely inform (age, presentation, the decision point where the controversy bites)',
    style: null,
  },
  motorcycling: {
    audience:
      'a year-round sport-naked rider (Triumph Street Triple 765 RS) who does his own maintenance and upgrades and cares about riding skill, gear, and the engineering behind it',
    sourceRules:
      'Serious moto journalism (Cycle World, MCN, Bennetts BikeSocial, RevZilla Common Tread, Motorcyclist), manufacturer/industry technical pages, rider-training and crash-research publications, and high-quality YouTube videos (NotebookLM ingests YouTube URLs — FortNine, respected coaching channels) are all ideal. Forum threads only if genuinely expert.',
    lenses: {
      authority:
        'INDUSTRY AND TECHNICAL AUTHORITY. Manufacturer technical material, standards bodies (ECE 22.06, CE gear ratings), tire manufacturer tech briefs, and serious moto-journalism explainers. Use mcp__claude_ai_Exa__web_search_exa.',
      community:
        'EXPERT RIDER/MECHANIC COMMENTARY. High-quality YouTube (FortNine, coaching channels like YCRS/ChampU, Life at Lean, CanyonChasers), respected long-form blog posts, and well-argued technical breakdowns. YouTube URLs are fine — NotebookLM ingests them. Use mcp__claude_ai_Exa__web_search_exa.',
      evidence:
        'EVIDENCE AND SCIENCE. Peer-reviewed crash/injury research, rider-training outcome studies, tire/vehicle-dynamics engineering literature. Use mcp__claude_ai_Semantic_Scholar__search_databases or mcp__claude_ai_Exa__deep_search_exa. Applied takeaways over pure theory.',
      controversy:
        'CONTROVERSY MAP. The 2-4 genuinely contested questions riders and coaches argue about (e.g. trail braking on the road, tire pressure dogma, airbag value, engine braking vs brakes). Return source pairs on opposite sides with "stance" set. If a debate is actually settled, say so.',
    },
    curatorPersona:
      'You are the EDITOR of a scenario-based motorcycling podcast: two riding-obsessed co-hosts who wrench on their own bikes and back every claim with a source.',
    scenarioHint:
      'a concrete riding or garage scenario (a specific corner, a specific upgrade decision, a specific near-miss) that the chosen sources genuinely inform',
    style:
      "Frame this as a scenario-based discussion between two riding-obsessed co-hosts who do their own wrenching: 'what would you actually do on the road / in the garage' framing rather than a spec-sheet read-through. Include moments of genuine disagreement that get resolved by citing the sources. Keep it conversational and energetic, but always land on clear, actionable takeaways a rider can use this weekend.",
  },
  photography: {
    audience:
      'an enthusiast photographer shooting a Nikon Z6 III (stills-first, real-world subjects, edits his own RAWs)',
    sourceRules:
      'DPReview, PetaPixel, Fstoppers, Thom Hogan (zsystemuser.com), imaging-resource, manufacturer technical pages, and high-quality YouTube tutorials (NotebookLM ingests YouTube URLs) are all ideal.',
    lenses: {
      authority:
        'TECHNICAL AUTHORITY. Manufacturer documentation, in-depth reviews (DPReview, imaging-resource, Thom Hogan), and rigorous technique references. Use mcp__claude_ai_Exa__web_search_exa.',
      community:
        'EXPERT PRACTITIONER COMMENTARY. Working photographers writing/filming about real workflows — PetaPixel, Fstoppers, respected YouTube educators. YouTube URLs are fine — NotebookLM ingests them. Use mcp__claude_ai_Exa__web_search_exa.',
      evidence:
        'EVIDENCE AND SCIENCE. Optics/sensor engineering explainers, perception and composition research, rigorous testing methodology (e.g. photonstophotos-style measurement). Use mcp__claude_ai_Exa__deep_search_exa or mcp__claude_ai_Semantic_Scholar__search_databases.',
      controversy:
        'CONTROVERSY MAP. The 2-4 genuinely contested questions photographers argue about within this topic (e.g. AI denoise/editing ethics, ETTR vs auto, primes vs zooms for the use-case). Return source pairs on opposite sides with "stance" set. If a debate is actually settled, say so.',
    },
    curatorPersona:
      'You are the EDITOR of a scenario-based photography podcast: two working photographers who argue about craft and back every claim with a source.',
    scenarioHint:
      'a concrete shooting or editing scenario (a specific shoot, light situation, or workflow decision) that the chosen sources genuinely inform',
    style:
      "Frame this as a scenario-based discussion between two working photographers: 'what would you actually do on this shoot / at this edit' framing rather than a spec-sheet read-through. Include moments of genuine disagreement that get resolved by citing the sources. Keep it conversational and energetic, but always land on clear, actionable technique the listener can try on their next shoot.",
  },
  tech: {
    audience:
      'a Linux power user running a substantial homelab (Arch workstation, VPS fleet, self-hosted media stack, Docker, Tailscale)',
    sourceRules:
      'Project documentation, engineering blog posts, serious self-hosting writeups, conference talks, and high-quality YouTube deep-dives (NotebookLM ingests YouTube URLs) are all ideal. Avoid low-effort listicles.',
    lenses: {
      authority:
        'PRIMARY DOCUMENTATION AND MAINTAINER MATERIAL. Official project docs, release notes, maintainer blog posts, and postmortems. Use mcp__claude_ai_Exa__web_search_exa.',
      community:
        'EXPERT PRACTITIONER COMMENTARY. Serious self-hosting/homelab writeups, engineering blogs, and high-quality YouTube deep-dives. Use mcp__claude_ai_Exa__web_search_exa.',
      evidence:
        'EVIDENCE AND BENCHMARKS. Measured comparisons, security analyses, and real operational experience reports over marketing. Use mcp__claude_ai_Exa__deep_search_exa.',
      controversy:
        'CONTROVERSY MAP. The 2-4 genuinely contested questions in this topic (e.g. bare metal vs containers vs VMs, self-host vs SaaS for a given service, security tradeoffs of exposure methods). Return source pairs on opposite sides with "stance" set.',
    },
    curatorPersona:
      'You are the EDITOR of a scenario-based homelab/self-hosting podcast: two opinionated engineers who run real infrastructure and back every claim with a source.',
    scenarioHint:
      'a concrete homelab decision (a specific service to deploy, migration, or incident) that the chosen sources genuinely inform',
    style:
      "Frame this as a scenario-based discussion between two opinionated engineers who run real infrastructure: 'what would you actually deploy / how would this fail' framing rather than a feature-list read-through. Include moments of genuine disagreement that get resolved by citing the sources. Keep it conversational and energetic, but always land on clear, actionable decisions the listener can apply to their own lab.",
  },
}

const profile = DOMAIN_PROFILES[domain] || {
  ...DOMAIN_PROFILES.tech,
  audience: `a curious, technically minded enthusiast of ${domain}`,
  curatorPersona: `You are the EDITOR of a scenario-based podcast about ${domain}: two knowledgeable co-hosts who back every claim with a source.`,
}

const SOURCES_SCHEMA = {
  type: 'object',
  required: ['sources'],
  properties: {
    sources: {
      type: 'array',
      items: {
        type: 'object',
        required: ['url', 'title', 'why'],
        properties: {
          url: { type: 'string' },
          title: { type: 'string' },
          year: { type: 'integer' },
          stance: { type: 'string', description: 'which side of the controversy this supports, or "neutral"' },
          why: { type: 'string', description: 'one line: why this source, what is new' },
        },
      },
    },
    controversies: {
      type: 'array',
      items: { type: 'string' },
      description: 'genuinely contested questions found, one line each',
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['http_status', 'accessible', 'content_matches', 'reason'],
  properties: {
    http_status: {
      type: 'integer',
      description: 'HTTP status observed from the raw curl command — the actual number, never guessed',
    },
    accessible: { type: 'boolean', description: 'publicly fetchable without login/paywall' },
    content_matches: { type: 'boolean', description: 'page content actually matches the claimed title/topic' },
    credible_source: {
      type: 'boolean',
      description: 'the original publisher or a recognized authority — false for content farms, SEO aggregators, and scraper sites',
    },
    final_url: { type: 'string', description: 'final resolved URL after redirects, if different' },
    alternative_url: {
      type: 'string',
      description: 'if the URL is bot-blocked, a public equivalent for the SAME work (e.g. its PubMed abstract page), else empty',
    },
    reason: { type: 'string' },
  },
}

const EPISODE_SCHEMA = {
  type: 'object',
  required: ['topic_id', 'title', 'audio_format', 'sources', 'rationale', 'case_vignette'],
  properties: {
    topic_id: { type: 'string' },
    title: { type: 'string' },
    audio_format: { type: 'string', enum: ['debate', 'deep-dive'] },
    sources: {
      type: 'array',
      minItems: 4,
      maxItems: 6,
      items: {
        type: 'object',
        required: ['url', 'title', 'why'],
        properties: {
          url: { type: 'string' },
          title: { type: 'string' },
          why: { type: 'string' },
        },
      },
    },
    rationale: { type: 'string' },
    case_vignette: { type: 'string', description: '2-3 sentence concrete opening scenario for the hosts' },
  },
}

const shared = `
You are one research lens inside a podcast deep-research pipeline. The
listener is ${profile.audience}. The episode topic is:

  "${topic.title}" (id: ${topic.id}, domain: ${domain})
  seed search query: ${topic.query}
  expected format: ${topic.debate ? 'DEBATE — genuine, citable controversy' : 'deep-dive'}

Ground rules:
- Every URL you return MUST be freely, publicly fetchable — NotebookLM will
  ingest each page directly, so no paywalled or login-gated links.
  ${profile.sourceRules}
- Prefer material from the last 24 months; older landmark material is fine
  if it anchors a controversy.
- Do NOT return any of these already-used URLs/IDs: ${JSON.stringify(avoid)}
- Return at most 6 candidates, quality over quantity, each with a one-line
  "why" that says what it adds to THIS episode.
- Use MCP research tools when available: load them with
  ToolSearch("select:<tool-name>") first. If an MCP tool is unavailable,
  fall back to WebFetch (load via ToolSearch too if needed).
`

const LENSES = [
  { key: 'authority', prompt: `${shared}\nYour lens: ${profile.lenses.authority}` },
  { key: 'community', prompt: `${shared}\nYour lens: ${profile.lenses.community}` },
  { key: 'evidence', prompt: `${shared}\nYour lens: ${profile.lenses.evidence}` },
  { key: 'controversy', prompt: `${shared}\nYour lens: ${profile.lenses.controversy}` },
]

phase('Research')
log(`Researching "${topic.title}" (${domain}) through ${LENSES.length} parallel lenses`)
// Barrier justified: candidates must be deduplicated across ALL lenses before
// spending a verification agent on each.
const lensResults = await parallel(
  LENSES.map((l) => () => agent(l.prompt, { label: `research:${l.key}`, phase: 'Research', schema: SOURCES_SCHEMA }))
)

const normalize = (u) =>
  (u || '')
    .trim()
    .toLowerCase()
    .replace(/^http:/, 'https:')
    .replace(/\/+$/, '')
    .replace(/\?(utm|ref)[^#]*/, '')
const seen = new Set(avoid.map(normalize))
const candidates = []
const controversies = []
for (const r of lensResults.filter(Boolean)) {
  for (const c of r.controversies || []) controversies.push(c)
  for (const s of r.sources || []) {
    const key = normalize(s.url)
    if (!key || seen.has(key)) continue
    seen.add(key)
    candidates.push(s)
  }
}
const CAP = 12
if (candidates.length > CAP) log(`capping verification at ${CAP} of ${candidates.length} candidates (kept in lens order)`)
const toVerify = candidates.slice(0, CAP)
log(`${candidates.length} unique candidates, ${controversies.length} controversy lines; verifying ${toVerify.length}`)

const verifyPrompt = (c) => `
Adversarially verify one candidate source for a podcast pipeline. The claim:

  URL: ${c.url}
  Claimed title: ${c.title}

STEP 1 — MANDATORY, do this FIRST, no exceptions. Run exactly:

    curl -sL -o /dev/null -w "%{http_code} %{url_effective}" --max-time 25 "${c.url}"

Report that status code verbatim in http_status. This is the ground truth
that decides accessibility — NotebookLM's ingester is a plain automated
fetcher just like curl, so what curl sees is what NotebookLM gets. Your own
impression, WebFetch's rendered output, or knowing the paper is "open
access in principle" do NOT override it. Never guess the number; if the
command fails to produce one, report 0 and set accessible=false.

Rules, applied mechanically:
- http_status is 200 → may proceed to step 2.
- http_status is 401/403 (bot protection or login wall — common on
  onlinelibrary.wiley.com, sciencedirect.com, pmc.ncbi.nlm.nih.gov,
  and doi.org links that resolve to them) → accessible=false. No exceptions.
- Any other status (404, 5xx, 000/timeout) → accessible=false.

STEP 2 — only if status was 200. Fetch the content (WebFetch; load it via
ToolSearch("select:WebFetch") if needed) and decide:
- accessible: is there substantive readable content — at least a full
  abstract or article body? A page showing only "Purchase PDF" or "Log in
  to view" with no abstract is accessible=false even at HTTP 200. Public
  YouTube video pages pass.
- content_matches: does the content actually match the claimed title and
  topic? Hallucinated DOIs, wrong papers, and redirects to unrelated pages
  fail.
- credible_source: is this the ORIGINAL publisher or a recognized authority
  in the field? Set false for content farms, SEO aggregators, and scraper
  sites that have republished someone else's work. Tells: a site whose
  navigation spans unrelated money-making verticals (insurance, loans,
  crypto, "advertise here"), a generic author byline with no other work on
  the subject, no editorial masthead, or text that appears verbatim
  elsewhere under a different byline. When a good article turns up on such
  a site, find the ORIGINAL and put that in alternative_url instead.

STEP 3 — rescue. If accessible=false but the work itself is real and
genuinely useful, find a public equivalent of the SAME work — its PubMed
abstract page (search
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&term=<url-encoded+title>[Title]
then use https://pubmed.ncbi.nlm.nih.gov/<pmid>/), a preprint, or an
open-access mirror. Curl that candidate the same way; only put it in
alternative_url if IT returns 200 with real content.

Default to FALSE when uncertain. Put the final resolved URL in final_url.`

// A candidate is usable if the agent's own verdict passes AND the HTTP status
// it observed is 200 — the status is the tiebreaker, because verify agents
// have talked themselves past bot-blocked publisher pages before. A rescued
// alternative_url (public equivalent of the same work) is accepted in place
// of a blocked original.
// credible_source defaults to true when the agent omits it, so an older
// verdict shape degrades to the previous behavior rather than rejecting
// everything.
const resolveUsable = (c) => {
  const v = c && c.verify
  if (!v) return null
  const credible = v.credible_source !== false
  if (v.accessible && v.content_matches && credible && v.http_status === 200) {
    return { ...c, url: v.final_url || c.url }
  }
  if (v.alternative_url && v.content_matches) {
    return { ...c, url: v.alternative_url, rescued_from: c.url }
  }
  return null
}

const runVerification = (items) =>
  parallel(
    items.map((c) => () =>
      agent(verifyPrompt(c), {
        label: `verify:${(c.url || '').replace(/^https?:\/\//, '').slice(0, 40)}`,
        phase: 'Verify',
        effort: 'low',
        schema: VERIFY_SCHEMA,
      }).then((v) => ({ ...c, verify: v }))
    )
  )

phase('Verify')
const verified = await runVerification(toVerify)
let usable = verified.filter(Boolean).map(resolveUsable).filter(Boolean)
const rescuedCount = usable.filter((c) => c.rescued_from).length
log(
  `${usable.length}/${toVerify.length} candidates survived verification` +
    (rescuedCount ? ` (${rescuedCount} via a public alternative URL)` : '')
)

// One supplemental round if curation would be starved.
if (usable.length < 5) {
  log('fewer than 5 usable sources — running one supplemental research round')
  const gapPrompt = `${shared}
Supplemental round: an earlier research pass found only ${usable.length}
usable public sources for this episode. Already-usable sources (do NOT
repeat): ${JSON.stringify(usable.map((u) => u.url))}. Rejected candidates
(do NOT repeat): ${JSON.stringify(toVerify.map((c) => c.url))}. Find up to 4
MORE candidates, favoring pages that are reliably public.`
  const extra = await agent(gapPrompt, { label: 'research:supplemental', phase: 'Research', schema: SOURCES_SCHEMA })
  const fresh = ((extra && extra.sources) || []).filter((s) => {
    const key = normalize(s.url)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  }).slice(0, 4)
  const extraVerified = await runVerification(fresh)
  usable = usable.concat(extraVerified.filter(Boolean).map(resolveUsable).filter(Boolean))
  log(`after supplemental round: ${usable.length} usable sources`)
}

if (usable.length < 4) {
  throw new Error(`only ${usable.length} verified public sources — not enough for an episode, aborting rather than shipping a thin one`)
}

phase('Curate')
const curatorPrompt = `
${profile.curatorPersona} You are curating one episode for the commute of
${profile.audience}. Topic: "${topic.title}" (id: ${topic.id}), episode date
${date}, default format ${topic.debate ? 'debate' : 'deep-dive'}.

VERIFIED, PUBLICLY ACCESSIBLE candidate sources (every URL here has been
checked — you may ONLY choose from this list, never invent or substitute
URLs):
${JSON.stringify(usable, null, 2)}

Controversy notes from the research pass:
${JSON.stringify(controversies, null, 2)}

Produce the episode brief:
- Pick the best 4-${maxSources} sources. If audio_format is "debate", the
  picks MUST include sources on genuinely opposing sides of at least one
  controversy (use the stance fields) so the hosts have something real to
  argue about. Rewrite each "why" as one crisp line: why this source, what
  is new.
- audio_format: keep "debate" only if there is live, citable controversy in
  the verified sources; downgrade to "deep-dive" if the evidence has
  genuinely converged (and say so in the rationale). Upgrade likewise.
- title: "${topic.title} — ${date}" or a sharper variant ending in "— ${date}".
- topic_id: exactly "${topic.id}".
- rationale: one paragraph — what is new, why these sources, why this
  format. This becomes editorial context for the hosts AND the notification
  blurb.
- case_vignette: 2-3 sentences: ${profile.scenarioHint}. The hosts will
  open the episode with it.`

const episode = await agent(curatorPrompt, { label: 'curate:editor', phase: 'Curate', effort: 'high', schema: EPISODE_SCHEMA })
if (!episode) throw new Error('curator agent returned nothing')

const chosen = new Set((episode.sources || []).map((s) => normalize(s.url)))
const allowed = new Set(usable.map((u) => normalize(u.url)))
for (const u of chosen) {
  if (!allowed.has(u)) throw new Error(`curator invented an unverified URL: ${u}`)
}

const payload = { ...episode }
if (profile.style) payload.style = profile.style

return {
  payload,
  stats: {
    domain,
    candidates: candidates.length,
    verified: usable.length,
    rescued: usable.filter((c) => c.rescued_from).map((c) => ({ from: c.rescued_from, to: c.url })),
    discarded: verified
      .filter(Boolean)
      .filter((c) => !resolveUsable(c))
      .map((c) => ({
        url: c.url,
        http_status: c.verify ? c.verify.http_status : null,
        reason: c.verify ? c.verify.reason : 'verification agent failed',
      })),
    controversies,
  },
}
