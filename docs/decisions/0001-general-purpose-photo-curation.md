# ADR 0001: General-purpose photo curation

Status: accepted (2026-11-01). Supersedes: none.

## Context

The product was built to choose photos for one narrow use: a person's social profile. The owner wants
an application whose purpose is finding beautiful photos, with no link to that use, and asked to remove
every trace of it.

The assumption goes beyond words. Prompts, score fields, a fixed six-slot set and a required face all
depend on it. A landscape cannot win today ([assessment](../assessment.md), A1).

## Decision

AuraFrame AI is a general photo curation tool. It ranks photographs by quality and visual appeal,
whatever the subject.

- No stage requires a person in the frame.
- The AI judges the photograph, and never a person's looks.
- The fixed slots become a diverse selection of configurable size.
- "Profile" becomes "library", a named collection with isolated data.
- A test keeps the retired vocabulary out of the repository.

## Alternatives

- **Rename only.** Rejected: faceless photos would still be excluded.
- **Keep the old purpose as a mode.** Rejected: the owner asked to remove every trace.
- **Add configurable scoring profiles.** Rejected for now: one good general ranking comes first.

## Consequences

- Prompts, schemas, database columns, UI text and tests all change (P01, P04).
- Existing analysis data becomes obsolete. There is no migration, because a rerun recreates it.
- Face signals remain as small quality penalties, and only when faces exist.
- Evidence: the regression corpus shows that a faceless photo can reach the shortlist.
