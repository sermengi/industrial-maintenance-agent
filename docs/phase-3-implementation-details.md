# Phase 3 (RAG Ingestion & Retrieval) — Implementation Decisions

Captured from planning discussion, 2026-08-16. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 3 and Dataset Design Specification v1.1 §4 without contradicting either. Nothing here has been implemented yet. Decisions are locked task-by-task, following the Phase 3 task list from the implementation plan:

1. Acquire/reference the approved English source documents and normalize only the selected sections/excerpts. **(locked)**
2. Create section-aware chunks preserving headings and necessary list/table context. **(locked)**
3. Attach the approved metadata contract, including `generic_reference` applicability. **(locked)**
4. Choose one embedding model and implement ingestion into pgvector. **(locked)**
5. Implement `search_maintenance_docs` with a compact structured result containing document/section identity and evidence text. **(locked)**
6. Add a repeatable ingestion command and avoid duplicate indexing. **(locked)**

---

## Task 1 — Source acquisition and normalization

### Content sourcing (the central decision for this task)

- **Excerpts are authored/representative text, not verbatim transcriptions of the real manufacturer manuals.** The five corpus items (DOC-01 through DOC-05) are written to match the real topic, structure, and technical substance described in Dataset Design Specification v1.1 §4 (Xylem Series 1710 §6/6.2 and §7.8/7.11, Bell & Gossett HSCS §6.1, Xylem TechnoForce e-MTX §6.15), but the indexed text itself is original writing, not a copy of the real manual pages.
- Rationale: this is a public portfolio repository. Reproducing verbatim copyrighted manual text carries real IP risk, whereas the project's own debug-first principle ("engineering fixture, not a realistic simulation") already grants latitude to author representative content rather than reproduce real-world source material exactly, as is already done for all of the synthetic structured plant data.
- Rejected alternative: short verbatim quotes wrapped in paraphrase, citing fair use — still requires locating and page-verifying real PDFs and re-litigating a fair-use judgment call. Rejected alternative: full verbatim excerpts from real manuals — highest IP exposure for a repo intended to be shown publicly, and depends on being able to legally source page-accurate manufacturer PDFs at all.
- **Content-authoring guardrail:** each authored excerpt must independently support its scenario's required evidence (Dataset Design Spec §10) on its own technical merits — e.g. DOC-03 must contain genuine vibration-troubleshooting guidance that would plausibly retrieve for a high-vibration query, not be written backward from the expected test answer. Rejected alternative: minimal/skeletal excerpts containing just enough to pass the retrieval test — would make Phase 3's retrieval tests trivially true rather than a real check of ingestion/embedding quality.

### Provenance labeling

- **`source_url` and `page` still point to the real manual/section the excerpt is modeled on**, functioning as an "inspired-by" reference rather than a verbatim citation. A new metadata field, `content_provenance: Literal["authored_representative"]`, makes this explicit on every document/chunk so nothing implies the indexed text is a direct transcription.
- Rejected alternative: dropping page-level citation and referencing only the manual's general product page — loses the page-locator detail the dataset spec table originally listed, for a downside (implying false precision) that the explicit `content_provenance` field already solves more directly.
- Real `source_url` values (actual product/manual page links) are an **open item deferred to implementation time** — not looked up during this planning session. Placeholder values are acceptable in the interim.

### Fixture location and format

- One Markdown file per document — `rag/corpus/sources/DOC-01.md` ... `DOC-05.md` — each with YAML frontmatter carrying the metadata contract (see Task 3), plus a `manifest.json`/`manifest.yaml` listing all five entries. Matches the dataset spec's own recommendation ("normalized text/Markdown fixtures with explicit metadata next to a source manifest") and the one-fixture-per-entity pattern already used in Phase 1/2.
- **Normalization** means: consistent Markdown structure (proper heading levels; checklists/troubleshooting steps as real Markdown lists/tables, not prose pretending to be a list), consistent units/terminology across all five docs, no PDF-artifact cruft (running headers/footers) — largely moot for authored text but stated so the convention is explicit.
- Each file holds the full normalized section text as a single unit. Splitting into retrieval chunks is explicitly Task 2's job, not Task 1's.

### Test / Validation

- [ ] All five `DOC-XX.md` fixtures exist with valid YAML frontmatter matching the Task 3 metadata contract.
- [ ] Each excerpt's technical content genuinely supports its scenario's required evidence (Dataset Design Spec §10) independent of any retrieval test passing.
- [ ] No excerpt is labeled or implied to be a literal manual for CP-200/CP-300 (see `applicability`, Task 3).
- [ ] `content_provenance: authored_representative` is present on every document.
- [ ] Manifest lists exactly five entries, one per `document_id`.

---

## Task 2 — Section-aware chunking

### Chunk granularity

- **Documents may be split into a small number of sub-chunks where they contain genuinely distinct sub-topics** (e.g. DOC-03's multiple vibration causes, DOC-01's multi-item inspection checklist), rather than forcing every document to exactly one chunk.
- **Split mechanism is structural, not computed.** Splits occur only at explicit subheadings that Task 1's authored Markdown already contains (e.g. a `####`-level heading per distinct cause/checklist group). The Task 2 chunker walks the document and cuts on those subheading boundaries — no runtime heuristic (token count, semantic similarity, etc.) decides where to split. This keeps chunking fully deterministic, consistent with the locked baseline ("section-aware deterministic chunking... no chunking hyperparameter experiment").
- **Which of the five documents actually get split, and into how many pieces, is not decided in this planning session** — it's a property of each document's real authored content (Task 1), decided at implementation time. What's locked here is the rule (split only on authored subheadings), not the outcome.
- Soft guardrail: at most ~3 sub-chunks per document, only where there are 2+ clearly distinct causes/checklist groupings — consistent with the spec's "one or a small number of coherent chunks."

### Chunk identity and self-containment

- Every chunk gets an ID of the form `{document_id}-C{n}` (e.g. `DOC-03-C1`, `DOC-03-C2`), always suffixed even for documents that end up with only one chunk. Keeps the schema uniform regardless of which documents get split, and means a document that starts single-chunk and later needs splitting doesn't require renaming its existing chunk.
- Each chunk's stored/embedded text includes both the parent section heading and its own subheading (if any) plus its body, so a chunk retrieved in isolation still reads coherently and cites correctly without depending on sibling chunks for context.

### Test / Validation

- [ ] Every chunk's `chunk_id` follows the `{document_id}-C{n}` pattern with no gaps or reuse.
- [ ] Chunk boundaries correspond exactly to authored subheadings in the Task 1 source file — no chunk splits mid-paragraph or at a computed offset.
- [ ] A chunk read in isolation (without sibling chunks) is self-explanatory — includes enough heading context to identify what it's about.
- [ ] Total chunk count across the corpus stays small (rough target: under ~20 for the whole 5-document corpus).

---

## Task 3 — Metadata contract

### Final metadata contract

**Document-level fields** (frontmatter on each `DOC-XX.md`, attached in Task 1, finalized here):

| Field | Type | Notes |
| --- | --- | --- |
| `document_id` | `str` | e.g. `DOC-01` |
| `manufacturer` | `str` | |
| `source_product_family` | `str` | |
| `section` | `str` | e.g. `7.11` |
| `page` | `str` | "inspired-by" reference, not a verbatim citation |
| `equipment_type` | `Literal["centrifugal_pump"]` | fixed for all v1 corpus items |
| `applicability` | `Literal["generic_reference"]` | fixed; enforces the "not a literal CP-200/CP-300 manual" guardrail at the type level |
| `source_url` | `str` | placeholder until implementation |
| `content_provenance` | `Literal["authored_representative"]` | fixed |
| `topic` | `Literal["HIGH_VIBRATION", "HIGH_BEARING_TEMPERATURE", "LOW_DISCHARGE_PRESSURE", "INSPECTION_PROCEDURE"]` | reuses `fault_taxonomy.canonical_name` vocabulary from Phase 1/2 where applicable, plus one procedural tag for content not tied to a single fault |
| `linked_fault_codes` | `list[str]` | zero, one, or more fault codes this document's content genuinely supports; exact values per document decided at implementation time |

**Chunk-level fields** (added at split time, Task 2): `chunk_id`, `chunk_heading: str | None`.

A retrieved chunk's full record is the parent document's metadata plus these two chunk fields plus the chunk text itself.

### Vocabulary bridge (the central decision for this task)

- **`topic` reuses the same fault-code vocabulary already locked in Phase 1/2** (`fault_taxonomy.canonical_name`) rather than an independent RAG-only tagging scheme, plus a small fixed set of non-fault procedural tags for content like DOC-01 that isn't tied to one fault. `linked_fault_codes` is a new field enabling multi-value traceability (a document can support more than one fault code).
- Rationale: keeps one shared vocabulary across the deterministic tool layer and the RAG layer instead of two independently-evolving tagging schemes, consistent with the plan's "one canonical contract across architecture, implementation, tests, and golden scenarios" principle.
- Rejected alternative: freeform `topic` strings scoped only to the RAG corpus — simpler to author in isolation, but creates two vocabularies for what's conceptually the same set of conditions and loses a natural hook for any future fault-code-aware retrieval behavior.
- **Scope boundary:** `linked_fault_codes`/`topic` are descriptive/citation metadata only in v1. Whether they're ever used to filter or boost retrieval is a Task 5 decision (resolved below: they are not used for filtering in v1).
- Physical storage shape of this metadata in pgvector (typed columns vs. JSONB) is a Task 4 decision, not decided here.

### Test / Validation

- [ ] Every document/chunk record validates against the fixed `Literal` fields (`equipment_type`, `applicability`, `content_provenance`, `topic`) — no free-text drift.
- [ ] `applicability` is `generic_reference` on all five documents, with no exceptions.
- [ ] Each document's `topic` value is one of the four defined literals; `linked_fault_codes` contains only fault codes present in Phase 1's `fault_taxonomy`.
- [ ] `page` and `source_url` are populated (placeholder acceptable pre-implementation) — never null.

---

## Task 4 — Embedding model and pgvector ingestion

### Embedding provider (the central decision for this task)

- **Hosted embedding API: Voyage AI** (`voyage-3-lite` as the default model choice, final dimension fixed by whichever specific model is selected at implementation time). Rationale: Anthropic has no native embeddings endpoint and recommends Voyage AI as its embedding partner for Claude-based RAG systems; staying in one ecosystem for both the future LLM provider (Phase 4) and the embedding provider keeps key/provider management simpler than introducing a second unrelated vendor (e.g. OpenAI) solely for embeddings.
- Rejected alternative: a small local open-source model (e.g. `sentence-transformers/all-MiniLM-L6-v2`) run in-container — was the initial recommendation for zero-secret, zero-cost, fully offline reproducibility matching Phases 0-2's testing story, but was set aside in favor of a hosted API per this decision. If a future phase finds the external dependency too costly/fragile, this decision should be revisited.
- **Accepted trade-off:** this makes RAG ingestion depend on an external API key end-to-end (local dev, CI, anyone cloning the repo) and a small per-call cost. See CI strategy below for how this is contained.

### CI strategy

- **CI uses a mocked/stubbed embedding client**, not live Voyage API calls. A thin `embed(texts: list[str]) -> list[vector]` interface (mirroring the same "thin provider-agnostic interface" pattern already planned for Phase 4's LLM client) is swapped for a deterministic stub in CI. Everything downstream of that seam — chunking, pgvector storage, the similarity query, upsert/reconciliation logic (Task 6) — runs against a real Postgres/pgvector instance in both environments; only the embedding *call* is faked.
- Rationale: keeps CI's zero-secret, zero-cost story from Phases 0-2 intact even though embeddings themselves are now hosted. The five golden-scenario retrieval assertions (vibration query → DOC-03, etc.) against the real embedding model are run locally/manually and documented as such, not part of automated CI.
- Rejected alternative: live API calls in CI with a committed GitHub Actions secret — more faithful end-to-end test on every PR, but introduces a paid external dependency, a secret to provision/rotate, and a new source of CI flakiness (rate limits, provider outages) that no earlier phase has had.

### pgvector schema and query mechanics

- One `rag_chunks` table with explicit typed columns per metadata field (not a JSONB blob) — consistent with Phase 1's relational, inspectable style. `linked_fault_codes` as a Postgres `text[]` column. `embedding` typed `vector(N)`, `N` fixed by the final Voyage model choice.
- **No ANN index** (no IVFFlat/HNSW). At ~15 chunks total, an approximate index is unnecessary complexity with its own tuning parameters (list count, etc.) — plain exact cosine-distance search (`ORDER BY embedding <=> query_embedding LIMIT k`) is simpler and fully deterministic at this scale.
- Upsert keyed by `chunk_id`: `INSERT ... ON CONFLICT (chunk_id) DO UPDATE`, using the deterministic IDs from Task 2. (Refined further in Task 6 with content-hash gating.)
- Embedding input text is exactly the chunk's self-contained text from Task 2 (heading + subheading + body) — no additional prefix/prompt engineering, keeping the pipeline untuned per the phase's constraints.

### Test / Validation

- [ ] `embed()` is called through the thin interface in both real and mocked configurations — no code path calls the Voyage SDK directly outside that seam.
- [ ] CI runs end-to-end against the mock backend with zero external network calls and no API key present.
- [ ] A local/manual run against the real Voyage backend confirms all five golden-scenario retrieval assertions from the Phase 3 plan.
- [ ] `rag_chunks` schema uses typed columns, not a JSONB metadata blob.
- [ ] No ANN index is present on the `embedding` column for v1.

---

## Task 5 — `search_maintenance_docs` tool

### Input contract

- `async def search_maintenance_docs(query: str, session: AsyncSession) -> SearchMaintenanceDocsResult` — a single free-text string, no other caller-configurable parameters. Same one-primitive-input precedent as `resolve_asset`/`get_plant_policy` (Phase 2).

### Retrieval mechanism (the central decision for this task)

- **Pure semantic search only** — always ranks all chunks by embedding similarity to `query`. No optional fault-code filter/boost parameter, despite the `linked_fault_codes`/`topic` bridge built in Task 3.
- Rationale: matches the "coarse-grained tools, no unnecessary parameters" precedent from Phase 2; with only ~15 chunks across 5 documents, semantic similarity alone is very likely sufficient to hit the required DOC-IDs for all eight golden scenarios, and nothing so far demonstrates a need for filtering. `linked_fault_codes`/`topic` remain valuable as citation/traceability metadata in the tool's output even though they're unused as an input filter.
- Rejected alternative: an optional `fault_code_hint: str | None` parameter that restricts candidates by `linked_fault_codes` before ranking — more deterministic/robust for known-fault scenarios in principle, but adds a second input path and fallback rule to test at a corpus scale where it likely isn't needed. Revisit if golden-scenario retrieval testing (Phase 8) shows pure semantic search missing required documents.

### Output contract

- `SearchMaintenanceDocsResult`: `query: str` (echoed back), `results: list[DocSearchHit]`.
- `DocSearchHit`: `chunk_id`, `document_id`, `section`, `page`, `topic`, `manufacturer`, `source_product_family`, `applicability`, `source_url`, `content_provenance`, `linked_fault_codes`, `evidence_text` (the chunk's stored text), `similarity_score: float`.
- The score is included for inspectability/telemetry (debug-first principle — same rationale as `get_asset_status`'s raw + classified readings pattern from Phase 2) even though the synthesis step isn't expected to quote it directly.

### top_k and empty-result handling

- `top_k` is a **fixed internal constant, not a caller parameter** (candidate default: 3) — same minimalism precedent as the rest of the tool contract. Exact number is an implementation-time tuning knob, not a design decision.
- For scenarios needing evidence from multiple documents in one turn (e.g. GS-05 needs DOC-02/DOC-05 *and* DOC-01), the expectation is that the Phase 4 graph calls `search_maintenance_docs` more than once with different refined queries rather than the tool trying to return everything relevant off one broad query — a Phase 4 orchestration concern, not a reason to inflate `top_k`.
- Results are cut both by `top_k` and by a minimum similarity threshold, so a query with no genuinely relevant corpus content can legitimately return `results=[]` — mirrors Phase 2's "empty list = no match, not an error" pattern (Phase 2 Task 6, Pattern 2). This is what gives Phase 5's later "insufficient evidence" guardrail something real to detect at the RAG layer. The threshold *mechanism* is locked now; the actual numeric value is deferred to implementation, once there's a real embedding model and real corpus to calibrate against.

### Tooling / location

- `tools/search_maintenance_docs.py`, same one-module-per-tool convention as Phase 2. Internally calls the Task 4 `embed()` interface on `query`, then runs the cosine-distance pgvector query.

### Test / Validation

- [ ] High-vibration query surfaces DOC-03 within `top_k`.
- [ ] Bearing-overheating query surfaces DOC-04.
- [ ] Low-discharge-pressure query surfaces DOC-05 and/or DOC-02.
- [ ] Seal-inspection query surfaces DOC-01.
- [ ] A deliberately irrelevant/nonsense query returns `results=[]` (threshold correctly excludes low-similarity noise).
- [ ] Every returned hit carries full source metadata sufficient for citation.
- [ ] Identical queries against a stable index return identical results — no nondeterminism in retrieval.

---

## Task 6 — Repeatable ingestion command and dedup

### Divergence from the Phase 1 bootstrap precedent (the central decision for this task)

- Phase 1's `maintenance-agent-db reset` truncates and reinserts every table on every run, deliberately rejecting upsert branching — because reinsertion is cheap and truncate+reinsert prevents stray rows from surviving a "reset."
- **RAG ingestion cannot follow that same mechanism**, because reinsertion here is not cheap — every insert requires a paid Voyage API call (Task 4). Truncate-and-reinsert would force a full re-embed of the whole corpus on every ingestion run, directly working against this task's "avoid duplicate indexing" requirement.
- **Resolution: content-hash-gated upsert with pruning**, which recovers Phase 1's "exactly matches the fixtures, no drift" guarantee through a different mechanism:
  - Compute the full desired chunk set from current Task 1/2/3 source fixtures.
  - For each desired chunk, compare a `content_hash` (over the embedded chunk text only, not surrounding metadata) against the stored value. Matching hash skips the embedding API call entirely. A changed or missing hash triggers `embed()` and an upsert of that row (`INSERT ... ON CONFLICT (chunk_id) DO UPDATE`).
  - Metadata columns (`source_url`, `page`, etc.) are refreshed unconditionally on every run regardless of hash match, since that's a free local write — e.g. fixing a placeholder `source_url` later doesn't require re-embedding.
  - Any DB row whose `chunk_id` is not in the current desired set is deleted (pruned).

### CLI shape

- A new console-script entry point mirroring Phase 1's mechanism (stdlib `argparse`, `project.scripts`), but a distinct command name since it's a logically separate subsystem: `uv run maintenance-agent-rag ingest`, plus a `--force` flag to bypass the content-hash skip and re-embed everything (needed if the embedding model ever changes).
- The same `ingest` command is invoked in both local dev and CI — CI runs it configured against the Task 4 mock `embed()` backend instead of the real Voyage client, exercising identical reconciliation/hash/prune logic either way.

### Test / Validation

- [ ] Running `ingest` twice against unchanged fixtures produces identical DB state.
- [ ] The second consecutive run triggers zero embedding calls (assertable directly against the mock in CI: call count == 0).
- [ ] Removing a chunk from source fixtures and re-running prunes its row from `rag_chunks`.
- [ ] Changing one chunk's authored text re-embeds only that chunk, not the whole corpus.
- [ ] `--force` re-embeds every chunk regardless of hash state.

## Success Criteria

- [ ] All five corpus documents exist as authored, normalized Markdown fixtures with complete metadata, independently supporting their required golden-scenario evidence.
- [ ] Chunking is deterministic and structural — reproducible from the same source fixtures with no computed/heuristic split logic.
- [ ] The full metadata contract (document- and chunk-level) is attached to every chunk, with `applicability=generic_reference` enforced by type on all five documents and `topic`/`linked_fault_codes` sharing vocabulary with Phase 1/2's `fault_taxonomy`.
- [ ] Ingestion into pgvector runs through the thin `embed()` interface, real (Voyage) in local/manual use, mocked in CI — with zero secrets or external calls required for CI to pass.
- [ ] `search_maintenance_docs` returns compact, fully-cited, structured results via pure semantic search, with a working empty-result path for irrelevant queries.
- [ ] Ingestion is repeatable and cost-conscious: unchanged content is never re-embedded, and the corpus in `rag_chunks` always exactly mirrors current source fixtures (no orphaned rows).
- [ ] All five approved corpus items are retrievable for their intended scenarios per the Phase 3 plan's own test list, verified locally against the real embedding model before Phase 3 is considered complete.

## Status

All six Phase 3 tasks are locked: source acquisition/normalization; section-aware chunking; metadata contract; embedding model/pgvector ingestion; `search_maintenance_docs`; repeatable ingestion command. Nothing has been implemented yet. Success Criteria for the milestone are defined above. Phase 3 planning is complete. Next: proceed to implementation, or move on to Phase 4 (LangGraph Agent Orchestration) planning discussion.