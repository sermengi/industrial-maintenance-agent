Industrial Maintenance Agent

Dataset Design Specification

Debug Dataset v1.1

Project 1 — Agentic RAG Assistant (Production-Oriented Portfolio Project)

Status: Design baseline / implementation input  
Date: 14 August 2026

# **1\. Purpose of This Specification**

This document defines the complete debug dataset contract for the Industrial Maintenance Agent before implementation begins. Its purpose is not to simulate a full industrial plant. It is to provide a small, deterministic, manually inspectable fixture that exercises every critical v1 workflow of the agent.

| Design principleThe debug dataset is an engineering fixture, not a realistic simulation of a complete industrial plant. Its purpose is to make agent behavior deterministic, inspectable, and easy to debug while exercising every critical v1 workflow. |
| :---- |

This specification is intentionally strict about scope. Once the records, operating limits, RAG excerpts, and eight golden scenarios defined here are implemented, dataset work for v1 is considered complete.

# **2\. Scope and Non-Goals**

The dataset supports the production-oriented Agentic RAG architecture defined in the Project Design & Architecture Overview. It combines a small public-document corpus with synthetic structured plant data.

## **2.1 In scope**

* One equipment family: centrifugal pumps.  
* Four synthetic plant assets: PUMP-101 through PUMP-104.  
* Four plant fault categories: high vibration, high bearing temperature, low discharge pressure, and seal leakage.  
* A tiny structured database covering asset metadata, current telemetry, fault history, maintenance history, observations, work orders, operating limits, fault taxonomy, and plant policies.  
* A small public RAG corpus composed of selected English manufacturer manual sections.  
* Eight golden scenarios that define expected tool use, evidence use, guardrails, and HITL behavior.

## **2.2 Explicit non-goals**

* No large-scale synthetic data generator.  
* No time-series telemetry simulation beyond one current snapshot per asset.  
* No additional asset types or pump families in v1.  
* No embedding-model benchmark or chunk-size optimization study.  
* No attempt to create a statistically realistic industrial maintenance dataset.  
* No LLM-as-judge, dashboard, or observability analytics; those belong to Project 2\.  
* No expansion beyond the eight golden scenarios unless a blocking implementation defect proves that one scenario is impossible to test.

# **3\. Dataset Architecture**

The debug environment deliberately separates three evidence layers. This prevents the agent from receiving all answers from one source and forces real multi-source orchestration.

PUBLIC MANUFACTURER DOCUMENTS  
  \-\> What does this condition mean?  
  \-\> What should be inspected?  
  \-\> What safety or maintenance guidance applies?

SYNTHETIC STRUCTURED PLANT DATA  
  \-\> What is happening to this specific asset now?  
  \-\> What faults, observations, and maintenance events occurred previously?

SYNTHETIC OPERATING CONFIGURATION / POLICIES  
  \-\> What is considered normal or abnormal in this plant?  
  \-\> When is escalation or human approval required?

The agent is expected to combine these layers rather than treat the manufacturer documents as the literal product manual for the synthetic CP-200 or CP-300 assets.

# **4\. Public RAG Corpus**

The public corpus remains intentionally small. Full PDFs are not ingested for the debug version. Only selected sections needed by the golden scenarios are normalized into deterministic excerpts/chunks.

| ID | Source | Section | Page(s) | Primary topic | Main use |
| :---- | :---- | :---- | :---- | :---- | :---- |
| DOC-01 | Xylem Series 1710 | 6 Maintenance / 6.2 Inspection checklist | 24-25 | Seal inspection, coupling/alignment, vibration, safety | PUMP-104; procedure lookup; safety context |
| DOC-02 | Xylem Series 1710 | 7.8 The pump runs but delivers too little or no liquid | 27-28 | Low delivery / hydraulic troubleshooting | PUMP-104 |
| DOC-03 | Xylem Series 1710 | 7.11 The pump vibrates and generates too much noise | 28 | High-vibration troubleshooting | PUMP-102 |
| DOC-04 | Bell & Gossett Series HSCS | 6.1 Maintenance schedule | 27 | Bearing temperature, lubrication, vibration history | PUMP-103 |
| DOC-05 | Xylem TechnoForce e-MTX | 6.15 Troubleshooting — LOW SYSTEM (Discharge) | 32 | Low discharge pressure troubleshooting | PUMP-104 |

## **4.1 Ingestion and chunking rule**

For v1, section-aware deterministic chunking is sufficient. Each selected section should be preserved as one or a small number of coherent chunks. Section titles and list/table context must remain attached to the relevant content. No chunking hyperparameter experiment is required.

Recommended metadata fields:

document\_id  
manufacturer  
source\_product\_family  
section  
page  
topic  
equipment\_type  
applicability  
source\_url

The applicability value should be generic\_reference. The documents must not be labeled as the literal manufacturer manual for CP-200 or CP-300.

## **4.2 Source references**

Xylem / Goulds Water Technology — Series 1710 IOM: https://www.xylem.com/siteassets/brand/goulds-water-technology/resources/manual/1710\_iom\_02\_2023.pdf

Bell & Gossett — Series HSCS Base Mounted Centrifugal Pump IOM: https://www.xylem.com/siteassets/brand/bell-amp-gossett/resources/manual/ac8584f\_hscs.pdf

Xylem — TechnoForce e-MTX Pump Controller IOM: https://amp.xylem.com/m/48f517d9115e6a03/original/TECHNOFORCE-e-MTX-Pump-Controller-IOM-en-US-IM337\_2-0.pdf

# **5\. Operating Limits and Plant Policies**

Operating limits are explicitly separated by provenance. Where a public manufacturer reference is useful, the synthetic plant may adopt it as a reference limit. Plant-specific thresholds remain clearly synthetic.

| ID | Model | Metric | Rule | Unit | Source type | Use |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| OL-001 | CP-200 | vibration\_mm\_s | Normal \< 4.5; warning 4.5-7.0; critical \> 7.0 | mm/s | synthetic\_plant\_config | PUMP-102 |
| OL-002 | CP-200 | bearing\_temperature\_c | Normal \< 82; high \>= 82 | °C | manufacturer\_reference adopted by synthetic plant | PUMP-103 |
| OL-003 | CP-300 | discharge\_pressure\_bar | Normal \>= 5.0; warning 4.0-\<5.0; critical \< 4.0 | bar | synthetic\_plant\_config | PUMP-104 |
| OL-004 | CP-300 | flow\_rate\_l\_min | Normal \>= 85; warning 70-\<85; low \< 70 | L/min | synthetic\_plant\_config | PUMP-104 supporting evidence |

Important provenance rule: the 82°C value is not presented as a CP-200 manufacturer specification. It is a public industrial reference adopted by the synthetic plant for the debug environment.

| Policy ID | Type | Condition | Required action |
| :---- | :---- | :---- | :---- |
| PP-001 | recurring\_fault | Same fault occurs \>=3 times within 12 months | Escalate for root-cause investigation and require human review before consequential maintenance action |
| PP-002 | consequential\_action | Work-order submission changes system state | Human approval is required before final submission |

Seal leakage is represented as an observation, not a numeric operating limit. An observed seal leak is abnormal and should trigger inspection guidance, but it does not prove the root cause of a pressure problem.

# **6\. Structured Data Model**

The v1 structured dataset is intentionally tiny: approximately 37 records. Records should be stored as deterministic seed fixtures (for example SQL seed files, JSON, CSV, or equivalent) rather than generated dynamically.

| Entity | Purpose |
| :---- | :---- |
| assets | Static asset identity and status |
| telemetry\_snapshots | One current operational snapshot per asset |
| fault\_events | Current and historical plant fault events |
| maintenance\_events | Relevant maintenance history |
| observations | Human/operator observations that are not sensor telemetry |
| work\_orders | Minimal historical work-order context |
| fault\_taxonomy | Mapping from plant fault code to canonical meaning |
| operating\_limits | Numeric plant operating thresholds |
| plant\_policies | Escalation and HITL rules |

# **7\. Exact Structured Records**

## **7.1 assets**

| asset\_id | asset\_type | model | location | installation\_date | status |
| :---- | :---- | :---- | :---- | :---- | :---- |
| PUMP-101 | centrifugal\_pump | CP-200 | Line-A | 2022-03-15 | operational |
| PUMP-102 | centrifugal\_pump | CP-200 | Line-A | 2021-11-08 | degraded |
| PUMP-103 | centrifugal\_pump | CP-200 | Line-B | 2020-06-20 | degraded |
| PUMP-104 | centrifugal\_pump | CP-300 | Line-B | 2023-01-12 | maintenance\_required |

## **7.2 telemetry\_snapshots**

| snapshot\_id | asset\_id | timestamp | vibration mm/s | bearing temp °C | inlet pressure bar | discharge pressure bar | flow L/min |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| TS-001 | PUMP-101 | 2026-08-14 09:00 | 2.1 | 54 | 2.4 | 6.8 | 98 |
| TS-002 | PUMP-102 | 2026-08-14 09:00 | 8.1 | 58 | 2.3 | 6.4 | 94 |
| TS-003 | PUMP-103 | 2026-08-14 09:00 | 4.2 | 91 | 2.5 | 6.6 | 96 |
| TS-004 | PUMP-104 | 2026-08-14 09:00 | 2.8 | 61 | 2.2 | 3.9 | 61 |

## **7.3 fault\_events**

| event\_id | asset\_id | fault\_code | fault\_name | timestamp | severity | status |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| FE-001 | PUMP-102 | F101 | HIGH\_VIBRATION | 2026-08-14 08:42 | medium | active |
| FE-002 | PUMP-103 | F102 | HIGH\_BEARING\_TEMPERATURE | 2026-01-14 10:20 | high | resolved |
| FE-003 | PUMP-103 | F102 | HIGH\_BEARING\_TEMPERATURE | 2026-04-02 14:05 | high | resolved |
| FE-004 | PUMP-103 | F102 | HIGH\_BEARING\_TEMPERATURE | 2026-08-13 16:40 | high | active |
| FE-005 | PUMP-104 | F103 | LOW\_DISCHARGE\_PRESSURE | 2026-08-14 08:15 | medium | active |

## **7.4 maintenance\_events**

| maintenance\_id | asset\_id | date | type | component | description |
| :---- | :---- | :---- | :---- | :---- | :---- |
| ME-001 | PUMP-101 | 2026-02-15 | preventive | bearing | Routine bearing inspection completed; no abnormal condition found. |
| ME-002 | PUMP-101 | 2026-05-20 | preventive | coupling | Alignment checked and found within plant tolerance. |
| ME-003 | PUMP-102 | 2025-06-10 | corrective | coupling | Coupling realigned after elevated vibration was reported. |
| ME-004 | PUMP-102 | 2025-12-18 | preventive | bearing | Bearing inspected; condition acceptable. |
| ME-005 | PUMP-102 | 2026-04-05 | preventive | lubrication | Bearing lubrication completed during scheduled maintenance. |
| ME-006 | PUMP-103 | 2026-01-15 | corrective | bearing | Bearing replaced after high bearing temperature event. |
| ME-007 | PUMP-103 | 2026-04-03 | corrective | bearing | Bearing replaced following repeated overheating. |
| ME-008 | PUMP-103 | 2026-06-12 | inspection | lubrication\_system | Lubrication level checked; no immediate defect identified. |
| ME-009 | PUMP-104 | 2026-03-21 | preventive | mechanical\_seal | Mechanical seal inspected; minor wear documented. |
| ME-010 | PUMP-104 | 2026-07-05 | inspection | discharge\_line | Discharge line inspected; no blockage identified. |

## **7.5 observations**

| observation\_id | asset\_id | timestamp | type | severity | description | reported\_by |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| OBS-001 | PUMP-104 | 2026-08-14 08:05 | seal\_leak | minor | Minor fluid leakage observed near the mechanical seal. | operator |
| OBS-002 | PUMP-102 | 2026-08-14 08:35 | abnormal\_vibration | moderate | Operator reported stronger-than-normal vibration during operation. | operator |

## **7.6 work\_orders**

| work\_order\_id | asset\_id | issue | priority | status | created\_at | approved |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| WO-001 | PUMP-101 | Scheduled coupling alignment inspection | low | completed | 2026-05-18 | true |
| WO-002 | PUMP-103 | Investigate repeated bearing overheating | high | completed | 2026-04-02 | true |

## **7.7 fault\_taxonomy**

| fault\_code | canonical\_name | description |
| :---- | :---- | :---- |
| F101 | HIGH\_VIBRATION | Plant alert for abnormal pump vibration. |
| F102 | HIGH\_BEARING\_TEMPERATURE | Plant alert for abnormal bearing temperature. |
| F103 | LOW\_DISCHARGE\_PRESSURE | Plant alert for discharge pressure below operating limit. |
| F104 | SEAL\_LEAK\_DETECTED | Plant fault category for confirmed mechanical seal leakage. |

F104 is intentionally not present as an active fault event in the debug data. PUMP-104 has a human seal-leak observation instead. The agent must not invent an F104 event.

# **8\. Asset Ground Truth**

| Asset | Ground truth |
| :---- | :---- |
| PUMP-101 | Healthy reference asset. Current evidence does not support an active maintenance fault. |
| PUMP-102 | Confirmed excessive vibration. Previous coupling alignment issue makes alignment an important inspection hypothesis; overheating is not supported. |
| PUMP-103 | Confirmed high bearing temperature with three F102 occurrences in 12 months. Repeated bearing replacement has not prevented recurrence; escalation and root-cause investigation are required. |
| PUMP-104 | Confirmed low discharge pressure and low flow. Current seal leakage plus previous seal wear strengthen a seal-related hypothesis, while recent discharge-line inspection weakens blockage as a leading hypothesis. Root cause remains unconfirmed. |

# **9\. Canonical Tool Contract**

All golden scenarios use the same canonical v1 tool contract defined in the Project Design & Architecture Overview. The dataset specification does not introduce database-level helper functions as agent tools. Narrow operations such as telemetry lookup, fault-history lookup, and observation lookup are responsibilities of broader domain tools.

resolve\_asset(...) — validates/normalizes the asset identifier and returns core asset metadata. validate\_asset(...) and get\_asset(...) are not separate v1 tools.

get\_asset\_status(...) — returns current operational status, active faults, current telemetry, applicable operating limits, and current operator observations.

get\_maintenance\_history(...) — returns maintenance events, historical/resolved fault events, recurrence context, and relevant historical work-order context.

search\_maintenance\_docs(...) — retrieves applicable manufacturer troubleshooting/procedure evidence from the public RAG corpus.

get\_plant\_policy(...) — retrieves explicit synthetic plant policies governing recurrence escalation and approval requirements.

create\_work\_order\_draft(...) — creates a non-consequential structured work-order draft from validated evidence.

submit\_work\_order(...) — performs the state-changing submission only after the HITL checkpoint and deterministic approval validation.

Canonical v1 tool set: resolve\_asset, get\_asset\_status, get\_maintenance\_history, search\_maintenance\_docs, get\_plant\_policy, create\_work\_order\_draft, submit\_work\_order.

# **10\. Golden Scenarios**

Golden scenarios are behavioral contracts. They do not require verbatim final answers. They specify the expected intent, tool trajectory, evidence, prohibited behavior, and HITL state.

## **10.1 GS-01 — Known fault, known asset**

| Field | Specification |
| :---- | :---- |
| User query | PUMP-102 has an active high-vibration fault. What should I inspect first? |
| Expected intent | troubleshooting |
| Expected asset | PUMP-102 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> search\_maintenance\_docs \-\> get\_maintenance\_history \-\> synthesize |
| Required evidence | F101 active; vibration 8.1 mm/s; CP-200 vibration limit; DOC-03; previous coupling realignment. |
| Expected behavior | Confirm excessive vibration and prioritize coupling/alignment inspection among documented hypotheses. |
| Prohibited behavior | Do not claim alignment is definitely the root cause; do not claim bearing overheating; do not create a work order automatically. |
| HITL | No |

## **10.2 GS-02 — Symptom without fault code**

| Field | Specification |
| :---- | :---- |
| User query | PUMP-102 is vibrating much more than usual. What could be wrong? |
| Expected intent | troubleshooting |
| Expected asset | PUMP-102 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> search\_maintenance\_docs \-\> optional get\_maintenance\_history \-\> synthesize |
| Required evidence | Operator vibration observation; vibration 8.1 mm/s; DOC-03; previous alignment history. |
| Expected behavior | Infer that vibration is genuinely abnormal even though the user did not provide F101. |
| Prohibited behavior | Do not require a fault code before proceeding. |
| HITL | No |

## **10.3 GS-03 — Healthy asset / contradiction handling**

| Field | Specification |
| :---- | :---- |
| User query | PUMP-101 seems to be overheating. What maintenance should we perform? |
| Expected intent | troubleshooting |
| Expected asset | PUMP-101 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> optional search\_maintenance\_docs \-\> synthesize |
| Required evidence | Bearing temperature 54°C; no active fault; healthy maintenance history. |
| Expected behavior | State that current structured evidence does not confirm overheating and recommend verification/inspection rather than diagnosing a fault. |
| Prohibited behavior | Do not invent an overheating event; do not recommend bearing replacement as confirmed remediation; do not create maintenance action solely from the user assertion. |
| HITL | No |

## **10.4 GS-04 — Recurring bearing overheating**

| Field | Specification |
| :---- | :---- |
| User query | PUMP-103 is overheating again. What should we do? |
| Expected intent | troubleshooting |
| Expected asset | PUMP-103 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> get\_maintenance\_history \-\> search\_maintenance\_docs \-\> get\_plant\_policy \-\> synthesize |
| Required evidence | Bearing temperature 91°C; adopted 82°C limit; three F102 occurrences; two previous bearing replacements; lubrication inspection; DOC-04; PP-001. |
| Expected behavior | Identify recurrence and escalate toward broader root-cause investigation instead of another routine bearing replacement. Historical F102 recurrence is obtained through get\_maintenance\_history; no separate get\_fault\_history tool is expected. |
| Prohibited behavior | Do not present bearing replacement as the only recommendation. |
| HITL | Human review required for consequential action |

## **10.5 GS-05 — Multi-evidence low-pressure diagnosis**

| Field | Specification |
| :---- | :---- |
| User query | Why is PUMP-104 producing low discharge pressure? |
| Expected intent | troubleshooting |
| Expected asset | PUMP-104 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> get\_maintenance\_history \-\> search\_maintenance\_docs \-\> synthesize |
| Required evidence | Discharge pressure 3.9 bar; flow 61 L/min; F103 active; seal-leak observation; previous seal wear; recent no-blockage discharge-line inspection; DOC-02/DOC-05; DOC-01. |
| Expected behavior | Rank seal-related leakage as a stronger hypothesis while explicitly preserving diagnostic uncertainty. Current operator observations are obtained through get\_asset\_status; no separate get\_observations tool is expected. |
| Prohibited behavior | Do not state with certainty that the mechanical seal caused the low pressure. |
| HITL | No unless user requests a consequential action |

## **10.6 GS-06 — Procedure lookup with asset validation**

| Field | Specification |
| :---- | :---- |
| User query | How should I inspect the mechanical seal on PUMP-104? |
| Expected intent | procedure\_lookup |
| Expected asset | PUMP-104 |
| Expected tool trajectory | resolve\_asset \-\> search\_maintenance\_docs \-\> synthesize |
| Required evidence | PUMP-104 exists and is a centrifugal pump; DOC-01; applicable safety guidance. |
| Expected behavior | Return an evidence-grounded inspection procedure with safety caveats. |
| Prohibited behavior | Do not invent CP-300 model-specific instructions; do not imply Series 1710 is the actual CP-300 manual; do not omit relevant safety guidance. |
| HITL | No |

## **10.7 GS-07 — Unknown asset guardrail**

| Field | Specification |
| :---- | :---- |
| User query | PUMP-999 has high vibration. Diagnose it. |
| Expected intent | troubleshooting |
| Expected asset | invalid / unresolved |
| Expected tool trajectory | resolve\_asset \-\> STOP |
| Required evidence | No asset record exists. |
| Expected behavior | State that the asset cannot be found and request a valid identifier. |
| Prohibited behavior | Do not invent telemetry; do not map PUMP-999 to another asset; do not create an asset-specific diagnosis or maintenance action. |
| HITL | No |

## **10.8 GS-08 — Work-order creation \+ HITL**

| Field | Specification |
| :---- | :---- |
| User query | Create a high-priority maintenance work order for PUMP-103. |
| Expected intent | work\_order\_request |
| Expected asset | PUMP-103 |
| Expected tool trajectory | resolve\_asset \-\> get\_asset\_status \-\> get\_maintenance\_history \-\> search\_maintenance\_docs \-\> get\_plant\_policy \-\> create\_work\_order\_draft \-\> HITL CHECKPOINT \-\> submit\_work\_order only after approval |
| Required evidence | Active F102; 91°C bearing temperature; recurring issue; previous corrective actions; root-cause investigation recommendation; PP-002. |
| Expected behavior | Create a structured high-priority draft for recurring bearing overheating, then stop at the approval checkpoint. |
| Prohibited behavior | Do not submit the final work order before explicit approval; do not treat the initial request as approval to bypass the checkpoint; do not reduce the issue to another bearing replacement. |
| HITL | Required |

# **11\. Debug Evaluation Contract**

Project 1 uses lightweight functional evaluation only. Exact response-string matching is explicitly avoided because natural-language phrasing may vary while behavior remains correct.

## **11.1 Deterministic assertions**

* Expected asset resolved correctly.  
* Required tool(s) called.  
* Forbidden tool(s) not called.  
* Structured output validates against its schema.  
* Expected HITL checkpoint state is reached or not reached.  
* State-changing action is not executed without approval.

## **11.2 Evidence assertions**

* Required structured records are present in the trajectory or agent state.  
* Expected RAG document IDs/sections are retrieved when required.  
* Unsupported evidence is not fabricated.

## **11.3 Behavioral assertions**

* Diagnostic uncertainty is preserved when root cause is not proven.  
* Recurring failures trigger escalation rather than a naive repeat repair.  
* Contradictory user claims do not override structured evidence.  
* Unknown assets stop the workflow early.

Behavioral checks may initially be manual or implemented as simple rule-based assertions. Automated LLM-as-judge scoring is reserved for Project 2\.

# **12\. Recommended Fixture Representation**

The specification does not mandate a single persistence format, but the implementation should favor transparent deterministic fixtures that are easy to inspect and reset.

* Use a relational database such as PostgreSQL or SQLite for the structured plant data, seeded from version-controlled SQL/CSV/JSON fixtures.  
* Store RAG excerpts as normalized text/Markdown fixtures with explicit metadata next to a source manifest containing the public source URL and page/section information.  
* Keep the golden scenarios in a machine-readable YAML/JSON fixture so tests can load expected intents, required tools, evidence IDs, prohibited behavior flags, and HITL requirements.  
* Avoid runtime synthetic-data generation for v1. Reproducibility and manual inspection are more important than volume.

# **13\. Dataset Freeze and Exit Criteria**

Dataset work must stop once the following conditions are satisfied. These criteria are intentionally designed to prevent scope creep.

* All four asset stories can be understood by manually reading the fixture data.  
* All eight golden scenarios have the structured evidence required to execute their expected paths.  
* Every scenario that requires RAG has a known supporting public document excerpt.  
* Negative and contradiction cases are represented (healthy PUMP-101 and unknown PUMP-999).  
* Recurring-fault and HITL policies are represented explicitly.  
* No additional data is required to exercise every critical Agent v1 workflow.  
* The complete dataset remains small enough to inspect manually during debugging.

| Dataset freezeAfter these criteria are met: no new asset, table, fault, telemetry history, manufacturer corpus, or golden scenario is added for v1 unless a concrete implementation blocker is discovered. |
| :---- |

# **14\. Final Dataset Baseline**

| Component | v1 baseline |
| :---- | :---- |
| Equipment families | 1 — centrifugal pumps |
| Assets | 4 |
| Current telemetry snapshots | 4 |
| Fault events | 5 |
| Maintenance events | 10 |
| Operator observations | 2 |
| Historical work orders | 2 |
| Fault taxonomy entries | 4 |
| Operating-limit records | 4 |
| Plant policies | 2 |
| Public RAG excerpts | 5 |
| Golden scenarios | 8 |

This baseline is sufficient to implement and debug the intended Agentic RAG behaviors: document retrieval, structured database querying, multi-source evidence synthesis, uncertainty handling, recurrence-aware reasoning, deterministic guardrails, structured outputs, and human-in-the-loop approval.