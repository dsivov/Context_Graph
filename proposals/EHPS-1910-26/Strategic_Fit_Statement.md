# Strategic Fit Statement

**RFP #1910-26 — AI-Based Staff Development and Professional Learning Platform**
**East Hartford Public Schools**

> *Draft for the bid team. The solution is delivered by **PolarTie** (AI mentor platform) **grounded by Context Graph** (the district-aligned **index tier**). Bracketed `[…]` items are placeholders to fill before submission. This section answers Submission Requirement #2 and is written to the three evaluation criteria EHPS names: **data isolation capabilities, K-12 focus, and localized model alignment.***

---

## 1. Our understanding of what EHPS is asking for

East Hartford Public Schools is not asking for another general-purpose AI tool. The RFP is explicit: the platform "must integrate directly with EHPS-specific policies, curriculum, instructional frameworks, technology systems, and operational documents, ensuring that the guidance staff receive is accurate, relevant, and consistent with how the district actually operates."

Meeting that bar requires two things working together: a capable AI mentor *experience*, and a *knowledge foundation* that keeps every answer grounded in East Hartford's own reality. Our solution provides both, through a partnership built for exactly this:

- **PolarTie** delivers the **AI mentor platform** — the conversational AI agents, multilingual support, interactive practice, role-based delivery, applications, and analytics staff interact with every day.
- **Context Graph** delivers the **index tier** — the district-aligned knowledge foundation the agents draw on: secure ingestion of EHPS materials, grounded retrieval with citations, policy lineage and validity, compliance checks, and strict per-tenant data isolation.

The two connect through an integration mechanism **both platforms already support**: PolarTie's *"External Knowledge via MCP — live AI connection to third-party or proprietary knowledge,"* and Context Graph's built-in MCP server. The result is a mentor that is fluent and easy to use *and* that answers from East Hartford's own documents, with sources — not a model guessing at district specifics.

## 2. Architecture at a glance

```
   EHPS staff  ──►  PolarTie AI mentor platform  ──MCP──►  Context Graph index tier  ──►  EHPS sources
 (certified &        (agents, voice, practice,            (ingestion, grounded                (policy binders,
  classified)         escalation, apps, analytics)         retrieval + citations,              frameworks,
                                                           lineage, compliance,                 curriculum,
                                                           per-tenant isolation)                tool/workflow docs)
```

**PolarTie (AI platform).** AI agents that "greet and assist immediately," are "fully customizable: voice, avatar, schedule, and prompt-driven personality," offer "multi-language support with real-time translation," escalate to humans with full session context, run on mobile/web/kiosk apps, and produce auto-transcription, summaries, and analytics dashboards with role-based access control — all on AWS, encrypted in transit and at rest.

**Context Graph (index tier — our scope).** The knowledge foundation those agents query. It ingests EHPS's policy binders, instructional frameworks, curriculum, and operational documents; builds a district-grounded knowledge base in which relationships carry *who decided, why, under which policy, and for how long*; returns answers assembled from EHPS's own materials **with source citations**; enforces district policy through a configurable compliance-rules layer; and keeps every district's data in its own isolated tenant.

This division puts the three criteria EHPS will evaluate — data isolation, K-12 focus, localized model alignment — squarely on the index tier, the part purpose-built for them.

---

## 3. Data Isolation Capabilities

*EHPS-named evaluation criterion. Reinforced at both tiers.*

- **Index tier (Context Graph).** East Hartford operates in its own logically isolated workspace. District documents, the derived knowledge graph, vector indexes, and all derived data are partitioned per-tenant across every storage layer (graph, vector, key-value, document status). There is no shared content pool; one district's data is never visible to another. **EHPS content is used solely to answer EHPS questions — never to train, fine-tune, or improve any shared or foundation model.**
- **Platform tier (PolarTie).** All interactions are "stored within your tenant and not accessible externally," your organization "retains full ownership" of uploaded knowledge, configurations, transcripts, and session data, and PolarTie commits: "We do not use your data to train global AI models" and "we never sell or share your customer data." Sessions are encrypted in transit and at rest on AWS, with administrator-controlled retention.
- **Deployment.** [Specify the deployment model offered to EHPS — dedicated tenant / VPC / data-residency commitment — across both tiers.]
- **Auditability.** Every retrieval and compliance decision in the index tier carries provenance, producing an audit trail suitable for district and state review. [Bid team: confirm FERPA/CT student-data-privacy applicability — this is *staff* development, which lowers exposure — and attach the joint DPA / privacy posture.]

## 4. K-12 Focus

*EHPS-named evaluation criterion.*

- **Built around district documents.** The index tier treats board policy binders, instructional frameworks, curriculum models, and operational documents as first-class inputs. Policies carry the attributes districts care about — adoption and revision dates, legal/cross references, and supersession relationships — so the platform reasons about *which* policy is in force. (We have reviewed the East Hartford BoE policy repository structure as a representative ingestion source.)
- **The whole workforce.** The mentor serves **both certified and classified staff**, with PolarTie delivering role-appropriate guidance for teachers, paraeducators, administrators, and operational staff.
- **Calendar- and cycle-aware.** District life runs on a calendar — onboarding, evaluation cycles, mandated-training windows, seasonal workflows. The index tier's validity windows and compliance rules drive timely, district-relevant prompts (the RFP's "Contextual Nudges").
- **A district ready for this.** East Hartford's selection for Connecticut's AI-in-education pilot signals readiness to adopt AI responsibly. Our emphasis on accuracy, citations, and compliance is built for exactly that — innovation the district can defend to its Board, its staff, and the state.

## 5. Localized Model Alignment

*EHPS-named evaluation criterion — the heart of the requirement, and the core of the index tier.*

- **Alignment by grounding, not guessing.** Rather than fine-tuning a generic model and hoping it absorbs district specifics, the index tier grounds every PolarTie response in EHPS's own ingested documents and returns the source. Answers are *constructed from* East Hartford's materials, so "how the district actually operates" is the substance of the answer.
- **Decision lineage keeps guidance current.** The knowledge base captures the *why/who/when* behind district relationships and policies, including validity periods. When a policy is revised or superseded, guidance reflects the version in force and does not drift out of date.
- **Compliance is enforced, not assumed.** A configurable rules layer checks guidance and records against district policy (required trainings, role prerequisites, approval limits) and flags exceptions with an auditable, human-readable reason. The district can express these rules in plain language; an assisted authoring step turns district policy statements into enforceable checks, validated before they go live.
- **Human-in-the-loop tuning.** Educator feedback and district-expert review feed back as first-class records; per-answer confidence is tracked; our team performs continuous configuration tuning so alignment keeps pace with evolving district practice — satisfying "Fine-Tuning & Accuracy" with an ongoing process, not a one-time setup.
- **Always current.** Automated ingestion (daily / weekly / monthly, per the RFP) keeps the index synchronized with EHPS source materials as policies, frameworks, and curricula change.

---

## 6. How the partnership meets the Scope of Work

| Scope of Work element (RFP) | PolarTie (platform) | Context Graph (index tier) |
|---|---|---|
| **Ask AI** — query policies/workflows/curricula; answers aligned to EHPS docs | Conversational agent, multilingual, voice/app delivery | Grounded retrieval over EHPS materials with **source citations** + policy lineage |
| **Role-Play Simulations** — practice parent meetings, coaching, feedback | AI agents with customizable persona/voice run the interactive practice | Supplies district-grounded scenarios, norms, and reference framing |
| **ToolAssist Guidance** — how/when to use internal tools | In-context delivery to staff | Grounded guidance over district tool/workflow documentation |
| **Contextual Nudges** — calendar-aligned prompts | Scheduling/delivery of prompts to users | Validity-window + rules-driven triggers (training windows, eval milestones) |
| **Role-Based Security** — tailor to role + clearance | Role-based access control, per-license configuration | Content/source filtering by role and clearance at retrieval |
| **District Integration** — ingestion/API mapping; automated updates | Knowledge connection via MCP | Secure ingestion of binders/frameworks/curriculum; daily/weekly/monthly sync |
| **Fine-Tuning & Accuracy** — continuous HITL | Agent prompt/config tuning; feedback capture in-product | Confidence scoring, provenance, captured corrections, ongoing alignment tuning |
| **Implementation & Adoption** — launch, onboarding, training | Guided onboarding, training blocks, Dedicated Account Manager | Source onboarding, ingestion setup, accuracy validation with district experts |
| **Accountability & Analytics** — usage analytics, engagement | Analytics dashboards, transcripts, summaries, review touchpoints | Retrieval/compliance audit data feeding reporting |

## 7. Why this is the right fit — beyond general-purpose AI

EHPS drew the line itself: *not* a general-purpose tool. The partnership is built along exactly that line — PolarTie makes it easy to use; Context Graph makes it true.

- **Defensible answers.** Every response cites its EHPS source — critical for a public district answerable to its Board, staff, and the state.
- **Current, not stale.** Decision lineage and validity windows keep guidance aligned to the policy actually in force.
- **Compliant by design.** District policy becomes enforced, auditable checks — "accurate and compliant everyday guidance," as the RFP asks.
- **Private by architecture.** Per-tenant isolation at both tiers and a firm "your data is never training data" commitment.
- **Built for districts.** Policy structure, certified/classified roles, and the school calendar are native concepts in the index tier.
- **Proven integration pattern.** The MCP connection between the platform and the index tier is a capability both products already support — not custom glue invented for this bid.

We respectfully submit that this architecture does not merely meet RFP #1910-26 — it is built around the precise distinction East Hartford drew between district-aligned and general-purpose AI.

---

## 8. Notes for the bid team (remove before submission)

- **Scope reflects the partnership:** Context Graph is contracted as the **index tier**; PolarTie delivers the AI mentor platform. Keep responsibility lines as drawn in §6. [Confirm prime/sub structure and which entity submits the response.]
- Fill all `[…]`: legal entities, deployment/data-residency, privacy/DPA attachments, role/clearance model, and confirm PolarTie feature specifics (role-play, analytics, RBAC) against their current product before claiming them.
- **Verify the MCP integration end-to-end** against PolarTie's "External Knowledge via MCP" before submission, and capture a short reference architecture diagram for the response.
- Confirm Connecticut student-data-privacy / FERPA applicability (staff development lowers but does not eliminate exposure) and attach the joint posture.
- Pair this with: the Requirement→Capability traceability matrix, Project References (3, joint or per-partner), Appendix A pricing (200 users × 2 years, all-inclusive), and required submittal forms.
- Timeline: questions due July 1, 2026; proposals due **July 10, 2026, 12:00 PM**.
