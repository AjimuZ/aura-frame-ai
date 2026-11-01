# ADR 0008: Minimal Pixel JRPG design language

Status: accepted (2026-11-01), at the owner's request. Implementation planned in P06. Supersedes: the "soft
premium" styling rules of the previous design document.

## Context

The owner asked to adopt the Minimal Pixel JRPG conventions. The previous design rules asked for soft
radii and shadows, and explicitly avoided a gaming look. The existing palette, AuraFrame Coral Night, is
defined in `_tokens.scss`, and the conventions forbid changing an existing palette.

## Decision

Apply Minimal Pixel JRPG to the existing color tokens: 80% modern minimal, 20% pixel identity. Flat
surfaces, 1 px frames, 0 to 2 px radii, stepped cursors, segmented gauges, no shadows or glow. Details are
in [Design system](../design-system.md).

Colors stay unchanged, with one correction to how they are used: primary button labels move to
`--af-bg-deep` on `--af-primary-soft`, because white on `--af-primary` measures 3.36:1 and fails AA.

## Alternatives

- **Keep the soft premium styling.** Rejected: the owner asked for the new conventions.
- **New palette for the new look.** Rejected: the conventions keep the project's existing colors.
- **Full retro look with pixel fonts everywhere.** Rejected: small pixel fonts hurt legibility, and the
  interface must not compete with the photos.

## Consequences

- All shared styles change, while the feature screens mostly keep their structure.
- Gauges must show real values only, which fits the rule against invented scores.
- Evidence: the checks in [Design system](../design-system.md#verification).
