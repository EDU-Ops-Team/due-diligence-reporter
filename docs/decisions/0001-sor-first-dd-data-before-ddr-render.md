# 0001 - SOR-First DD Data Before DDR Render

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Greg, Codex

## Context

The due-diligence workflow historically exposed normalized report data to the
pipeline only when the agent called `create_dd_report`. That made the Google Doc
render path the practical publication boundary for structured due-diligence
data, even though Rhodes / LocationOS is the operating system of record. Greg
wants the workflow to publish known structured data to the SOR as soon as it is
available, log what changed and why, and use the DDR only as a consolidated view
when one is needed. The existing Google Doc renderer also carries overwrite
guards and candidate behavior that should remain intact while the data boundary
moves earlier.

## Decision

We will split normalized due-diligence data preparation from DDR rendering and
publish SOR-ready DD fields to Rhodes before creating or updating a DDR Google
Doc.

## Consequences

- Positive: Rhodes can become the first durable publication point for DD facts,
  with the DDR acting as a supporting view rather than the primary record.
- Positive: Candidate DDRs no longer block structured DD field updates when the
  active DDR is protected by manual-edit guards.
- Positive: The pipeline can log the SOR write outcome before rendering and
  include that outcome in the P1 DRI note.
- Negative: The pipeline now has a two-phase report path, so render failures can
  happen after a successful SOR write and must remain visible in the run
  manifest and P1 note.
- Negative: Existing agent prompts and tests must keep the prepare-data tool
  ahead of the render tool, or older callers may continue using the legacy
  render-first path.
- Neutral: `create_dd_report` remains available for compatibility, but the
  shared pipeline treats `prepare_due_diligence_data` as the preferred boundary.

## Alternatives considered

Keep `create_dd_report` as the only handoff and write Rhodes after rendering.
This preserved the existing control flow, but it kept the Google Doc as the
first point where normalized data became durable and did not support the desired
SOR-first operating model.

Make `create_dd_report` update Rhodes internally before rendering. This would
have produced SOR-first writes for that one tool call, but it would have mixed
Rhodes mutation responsibility into the server-side renderer and made P1 note
sequencing harder to reason about from the pipeline.

Rewrite the agent as a fully separate extraction service before touching the
current pipeline. This is the cleaner long-term boundary, but it is too large
for a safe first slice because the existing tool-calling agent, enrichment
tools, overwrite guards, and validation tests already depend on the current
pipeline shape.
