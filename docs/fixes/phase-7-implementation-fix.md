# Finding — LangGraph checkpoint serializer warns on unregistered custom types

Captured 2026-08-21, during a notebook walkthrough of the Phase 7 structured-telemetry
scenarios (`phase7structuredtelemetrywalkthrough.ipynb`), Scenario A (section 4, normal
`/agent/query` completion for PUMP-102). Not a Phase 7 defect — the telemetry event itself
was correct — but a cross-cutting checkpointer/dependency issue surfaced by exercising the
real `build_agent_graph`/`MemorySaver` path end-to-end for the first time in a debug
session. Not yet fixed.

## What was observed

Running Scenario A printed five repeated warnings before the response/event JSON, one per
custom type carried in graph state:

```
Deserializing unregistered type maintenance_agent.db.repositories.records.AssetRecord from checkpoint.
This will be blocked in a future version. Set LANGGRAPH_STRICT_MSGPACK=true to block now, or add to
allowed_msgpack_modules to allow explicitly: [('maintenance_agent.db.repositories.records', 'AssetRecord')]
```
...repeated for `ToolCallRecord`, `ClassifiedReading`, `DocSearchHit`, and `AgentQueryResponse`.

These fired even though this scenario never calls `interrupt()` — it's a plain
non-HITL completion. That's expected, not a bug in the scenario: per Phase 6 Task 3, the
compiled graph carries a `MemorySaver` checkpointer unconditionally, so every run's state is
serialized at each step regardless of whether a pause happens. Any state field holding one
of these project-defined types (`state["asset"]`, `state["tool_calls"]`,
`state["structured_evidence"]`, `state["document_evidence"]`, `state["response"]`) goes
through the checkpointer's serializer on every single invocation.

## Root cause

`langgraph-checkpoint`'s `JsonPlusSerializer` (used by `MemorySaver` and every other
checkpointer backend) falls back to a constructor-based reconstruction path for any Python
object it doesn't have a native encoding for — encoding the object's module + class name
alongside its field data, then re-invoking the constructor on load. That fallback path is
exactly what CVE-2025-64439 / [GHSA-wwqv-p2pp-99h5](https://osv.dev/vulnerability/GHSA-wwqv-p2pp-99h5)
addressed: unrestricted constructor-based deserialization from checkpoint data is a
deserialization-of-untrusted-data risk (potential arbitrary code execution if the
checkpoint payload itself is attacker-influenced). The fix, shipped in
`langgraph-checkpoint==3.0.0`, adds an allow-list for which module/class combinations may be
reconstructed this way. The warning text we're seeing is that transition in progress: right
now an unlisted type still deserializes (with a warning), but a future release — or setting
`LANGGRAPH_STRICT_MSGPACK=true` today — makes that a hard error instead.

None of this project's five flagged types (`AssetRecord`, `ToolCallRecord`,
`ClassifiedReading`, `DocSearchHit`, `AgentQueryResponse`) are on any default allow-list,
because they're project-defined, not part of `langgraph`/`langgraph-checkpoint` itself.

## Why it matters

- **Forward compatibility, not (currently) a live security hole.** This project's
  `MemorySaver` is in-memory and per-process (Phase 6 Task 3's deliberate choice, "not
  something redeployed mid-demo"); no external actor supplies checkpoint bytes, so the
  RCE risk the CVE describes doesn't apply to this project's actual threat model today.
  But an untouched dependency bump (`langgraph-checkpoint` moving to whatever version
  finishes flipping this to strict-by-default) would turn every one of these warnings into
  a hard failure — meaning `resolve_asset`'s result, the tool-call trace, the accumulated
  evidence, and the final response would all stop round-tripping through checkpoint
  save/restore. That would break not just Phase 6's HITL pause/resume (which depends on
  `graph.aget_state(config).values` returning intact typed objects, per Phase 6 Task 4 and
  Phase 7 Task 2's decision to read `tool_calls` the same way for a paused turn-1 event) but
  every completed run, since checkpointing isn't scoped to HITL paths.
- **Noise today.** Five warnings per API call already clutters notebook/log output for a
  project whose stated design principle is "optimize for inspectability" (Phase 1 debug-first
  philosophy) — the opposite of what this warning is currently doing to the walkthrough.

## Recommended fix

Register this project's checkpoint-carried types explicitly, rather than waiting for a
default-allow behavior to disappear out from under the app. Per LangGraph's own forum
guidance ([How to register a type](https://forum.langchain.com/t/how-to-register-type-in-langgraph/3456)),
`langgraph.json`-level configuration does not currently take effect for this
([open feature request](https://forum.langchain.com/t/feature-request-wire-allowed-msgpack-modules-in-langgraph-json/3624)) —
the working mechanism is constructing the serializer in code and passing it into the
checkpointer:

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from maintenance_agent.db.repositories.records import AssetRecord, WorkOrderRecord
from maintenance_agent.orchestration.state import ToolCallRecord
from maintenance_agent.tools.get_asset_status import ClassifiedReading
from maintenance_agent.tools.search_maintenance_docs import DocSearchHit
from maintenance_agent.schemas.agent import AgentQueryResponse
# plus any other project types that ever land in graph state:
# FaultEventRecord, FaultRecurrence, PlantPolicyRecord, WorkOrderDraft, RunEvent (if ever
# checkpointed — currently it isn't, per Phase 7 Task 2's route-level-only handling)

serde = JsonPlusSerializer(
    allowed_msgpack_modules=[
        AssetRecord,
        WorkOrderRecord,
        ToolCallRecord,
        ClassifiedReading,
        DocSearchHit,
        AgentQueryResponse,
        # ... every other custom type reachable from GraphState
    ]
)
checkpointer = MemorySaver(serde=serde)
```

This fits the project's existing lifecycle pattern exactly — the checkpointer would be
constructed once, alongside the compiled graph, at FastAPI `lifespan` startup (Phase 4 Task 6's
"own the seam" pattern already used for the DB engine and the compiled graph), not rebuilt
per request.

**Caveat worth resolving empirically before implementing**: forum reports differ slightly
on what happens to a type left *off* the list once `allowed_msgpack_modules` is passed —
one report says it degrades unlisted types to plain `dict` rather than erroring, which
would be silently worse than today's warn-and-succeed behavior if the list is incomplete.
Before wiring this in, enumerate every type that can reach `GraphState` (cross-check against
Phase 4 Task 1's full state field list, including anything nested inside `ToolResult`/
`StructuredEvidenceItem` unions) and add a test that round-trips a real checkpoint through
`aget_state` for a paused GS-08-style run, asserting no field silently degrades to a raw
dict.

## Open questions

- What version of `langgraph-checkpoint` is currently pinned in `pyproject.toml`? Worth
  checking whether it already includes the CVE-2025-64439 fix (`>=3.0.0`) or predates it —
  affects urgency, not just the allow-list question.
- Should `RunEvent`/`ToolCallSummary` ever be excluded from this concern permanently, or
  could a future change route them through graph state (making them checkpoint-relevant
  too)? Per Phase 7 Task 2, currently no — they're assembled at the route layer from
  already-checkpointed pieces, never stored as their own state field, so they're out of
  scope for this fix as currently designed.

## Status

Implemented. The installed versions are `langgraph==1.2.11` and
`langgraph-checkpoint==4.2.0`. The warning reproduced on the Phase 7 normal-run JSONL
scenario before the fix, but `LANGGRAPH_STRICT_MSGPACK=true` did not reproduce a hard
failure on this installed version.

The graph now constructs `MemorySaver` with an explicit `JsonPlusSerializer` allow-list for
the project-defined types reachable from `GraphState`, including repository records,
tool-result models, evidence models, `ToolCallRecord`, `ErrorRecord`, `WorkOrderDraft`, and
`AgentQueryResponse`. Regression coverage asserts the configured allow-list, confirms a
paused HITL checkpoint restores typed project objects, and confirms the unregistered-type
warning is not emitted.
