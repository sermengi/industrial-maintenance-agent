# Finding — `evidence_used` citation subset not exposed in `AgentQueryResponse`

Captured 2026-08-19, during a notebook walkthrough of GS-06 (procedure lookup, PUMP-104,
"How should I inspect the mechanical seal on PUMP-104?") using
`phase45langgraphorchestrationreliabilitywalkthrough.ipynb`. Confirmed against the real
source (not just the notebook's state summaries) via a coding-agent code check. This is a
gap between what Phase 5 Task 6 (evidence provenance) intended and what's actually
implemented — not yet fixed.

## What was observed

In the GS-06 walkthrough, the evidence-gathering loop retrieved three RAG chunks
(DOC-01, DOC-02, DOC-05). The synthesis LLM grounded its answer in only one of them
(`evidence_used: ["DOC-01"]`), which passed Task 6's citation validator (real ID,
non-empty). But the final `AgentQueryResponse` returned to the API caller included all
three chunks in `document_evidence`, with no indication of which one was actually cited.
DOC-01, DOC-02, and DOC-05 appear equally relevant in the response body, even though the
answer only relied on DOC-01.

## Root cause (confirmed against source)

- `src/maintenance_agent/schemas/agent.py:48` — `AgentQueryResponse` defines
  `request_id`, `status`, `asset_id`, `answer`, `confidence`, `structured_evidence`,
  `document_evidence`, `pending_action`, `error`. No `evidence_used` or citations field.
- `src/maintenance_agent/orchestration/graph.py:129` — `SynthesisOutput.evidence_used`
  is defined and populated internally.
- `src/maintenance_agent/orchestration/graph.py:497` — stored into graph state as
  `synthesis_evidence_used`.
- `src/maintenance_agent/orchestration/graph.py:507` — builds the terminal
  `AgentQueryResponse` but never passes `state["synthesis_evidence_used"]` into it.
- `src/maintenance_agent/api/agent.py:16` — the route's `response_model=AgentQueryResponse`
  means the API contract itself exposes only the fields above; there's no way for a
  caller to get the citation subset even indirectly.

## Why this matters

Phase 5 Task 6 (`claude/phase5-decisions.md`) was built specifically so evidence
provenance would be surfaced in the final structured result, and its own test/validation
notes state the goal explicitly: "GS-01 through GS-06's expected evidence... can be
cross-referenced by ID between `evidence_used` and `structured_evidence`/`document_evidence`
in the final response — supporting Phase 8's 'required structured records are present'
assertion directly from the response body, not just internal state."

As implemented, the citation validator (existence + non-empty check) does real internal
work — it gates retries and prevents fabricated citations from ever reaching a terminal
state — but the actual signal it produces (which items the answer is grounded in, versus
which were retrieved but not relied upon) never reaches the API consumer. Downstream
consumers, including Phase 8's planned evidence assertions, cannot currently do "required
structured records are present in the response" checks against the citation subset — only
against the full retrieved set.

There's also a docs/code mismatch: `docs/phase-5-implementation-details.md:246` already
describes final responses as allowing cross-referencing of `evidence_used` against
evidence IDs, but the implementation does not currently expose that field. The docs
describe the intended behavior; the code doesn't yet match it.

## Decided fix (minimal version)

Add `evidence_used: list[str]` to `AgentQueryResponse` and pass
`state["synthesis_evidence_used"]` through at `graph.py:507` when building the terminal
response. No schema redesign, no filtering of `structured_evidence`/`document_evidence`
down to only-cited items — those lists keep returning everything retrieved, and the new
field lets a consumer cross-reference which subset was actually load-bearing.

Rejected alternative (discussed, not chosen): filtering `structured_evidence`/
`document_evidence` down to only cited items, or adding a `used: bool` flag per item.
Rejected for now as a bigger behavior change than needed to close the gap, and because it
would throw away the "what was looked at but not relied upon" context, which has its own
debugging value.

## Status

Confirmed, not yet implemented. Next step: add the field to `AgentQueryResponse` and wire
it through in `graph.py`'s terminal response assembly, then re-run GS-06 (and ideally
GS-01) through the notebook to confirm `evidence_used` in the response matches
`synthesis_evidence_used` in graph state.