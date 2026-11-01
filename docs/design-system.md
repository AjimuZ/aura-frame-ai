# Design system

Status: **planned** ([P06](roadmap.md)). The code still uses rounded cards and soft shadows. Decision:
[ADR 0008](decisions/0008-minimal-pixel-design-language.md).

## Principle

**Minimal Pixel JRPG: 80% modern minimal application, 20% pixel JRPG identity.** The result is a
professional photo tool with the discipline of a refined RPG menu. It is not a game. Readability
wins over retro fidelity. Photos are the focus, and the interface stays quiet around them.

## Color

**The palette does not change.** The existing tokens in
[`_tokens.scss`](../apps/desktop/angular/src/styles/_tokens.scss) stay as they are. New shades derive
from them with `color-mix()`. Hardcoded colors are not allowed.

| Role | Token | Value |
| --- | --- | --- |
| Background | `--af-bg-main` | `#2d2f42` |
| Panel | `--af-bg-surface` | `#36384d` |
| Raised surface | `--af-bg-elevated` | `#404259` |
| Inset surface | `--af-bg-deep` | `#262839` |
| Text | `--af-text-main` | `#ffffff` |
| Muted text | `--af-text-muted` | `#f3d8dd` |
| Subtle text | `--af-text-soft` | `#f7e6ea` |
| Brand | `--af-primary` | `#d46c7f` |
| Accent | `--af-primary-hover` | `#f6798c` |
| Soft accent | `--af-primary-soft` | `#e4a2ad` |
| Muted accent | `--af-primary-muted` | `#895164` |
| Success | `--af-success` | `#8fd6b5` |
| Warning | `--af-warning` | `#f0b86e` |
| Danger | `--af-danger` | `#d46c7f` |
| Info | derived | `var(--af-primary-soft)` |
| Border | `--af-border-soft` | `rgba(243, 216, 221, 0.14)` |

### Measured contrast

Computed from the tokens with the WCAG formula on 2026-11-01.

| Foreground on background | Ratio | Use |
| --- | --- | --- |
| `--af-text-main` on `--af-bg-main` | 13.15 | Text: yes |
| `--af-text-muted` on `--af-bg-main` | 9.82 | Text: yes |
| `--af-text-soft` on `--af-bg-surface` | 9.54 | Text: yes |
| `--af-primary-soft` on `--af-bg-main` | 6.31 | Text, focus: yes |
| `--af-primary-hover` on `--af-bg-main` | 5.03 | Text, focus: yes |
| `--af-success` on `--af-bg-surface` | 6.81 | Text: yes |
| `--af-warning` on `--af-bg-surface` | 6.44 | Text: yes |
| `--af-bg-deep` on `--af-primary-soft` | 6.97 | **Primary button label: yes** |
| `--af-bg-deep` on `--af-primary-hover` | 5.56 | **Primary button label: yes** |
| `--af-bg-deep` on `--af-primary` | 4.33 | Large text only |
| `--af-primary` on `--af-bg-main` | 3.91 | Borders, large text only |
| `--af-text-main` on `--af-primary` | 3.36 | Large text only. **Today's button labels use this and fail AA** |
| `--af-primary-muted` on `--af-bg-main` | 2.14 | Decoration only |

Rules: 4.5:1 for text, 3:1 for large text, focus rings and control borders.

## Non-color tokens

These are added next to the color tokens.

```scss
:root {
  --af-frame: color-mix(in srgb, var(--af-text-muted) 28%, transparent);
  --af-px: 2px;
  --af-radius: 2px;
  --af-quick: 120ms;
  --af-font-ui: system-ui, -apple-system, 'Segoe UI', sans-serif;
  --af-font-mono: ui-monospace, 'Cascadia Code', Consolas, monospace;
  --af-font-pixel: 'AuraFrame Pixel', var(--af-font-ui);
}
```

`--af-shadow-soft`, `--af-shadow-primary` and the 8 to 18 px radii are retired. Depth comes from
frames, layers and contrast. There are no shadows or glow.

## Primitives

| Primitive | Recipe | Used for |
| --- | --- | --- |
| Window | Flat panel, 1 px frame, stepped accent marks on two corners | Primary panels only |
| Card | Same frame, no corner marks | Secondary panels, photo cards |
| Menu entry | Stepped cursor drawn with `clip-path`, filled when selected | Sidebar, lists, tabs, current table row |
| Button | Framed, 2 px radius, one step of press feedback | All actions. Primary: `--af-bg-deep` label on `--af-primary-soft` |
| Field | Inset surface, 1 px frame, 2 px focus outline in soft accent | Forms |
| Tab | Framed top, reuses the menu cursor | Filters, settings groups |
| Gauge | Segmented HP-style bar from real values, at most 40 segments | Scores, pipeline progress |
| Unknown gauge | Neutral striped track | A value not measured yet. A number is never invented |
| Status pill | Square marker plus a word | Status: always a word as well as color |
| Activity line | Stepped sweep | Only while real work runs. It never implies a percentage |

## Screens

| Today | Target name | Notes |
| --- | --- | --- |
| Dashboard | Overview | Library state and last selection |
| Import | Library | Native folder dialog and library list |
| Analysis | Analysis | Eleven-stage list with gauges, and a comparison counter for the tournament |
| Review | Review | Grid, filters, burst alternates, A/B compare |
| The six-slot builder | Selection | Ordered selection with swaps and locks, no fixed slots |
| Reports | Reports | Links and downloads |
| Settings | Settings | AI provider, opt-in state, thresholds |

Layout: a vertical menu sidebar in the style of an inventory screen, compact headers, thin
separators, dense but legible information. The photo grid keeps the most space, and a photo's
pixels are never covered by decoration. Layouts must hold at the minimum window size with no horizontal
scroll.

## Icons and type

- Icons are drawn on a 16 by 16 grid, monochrome (`fill="currentColor"`), `crispEdges`, generated
  into one SVG sprite. Pixel icons are never mixed with other icon sets.
- Content uses the system font stack. Code, IDs and logs use monospace.
- A pixel font is optional, for short titles at 15 px and above, never for paragraphs, labels, tabs
  or small badges. It must be bundled locally under an open license, with its source revision, file
  hash and license recorded in third-party notices. Candidate: Pixelify Sans (SIL OFL), to confirm
  in P06.

## Motion

Brief and discreet: a fade or a 2 to 8 px slide in a few steps, about 120 ms. Nothing loops, except
the activity line while real work runs. `prefers-reduced-motion` removes the rest.

## Verification

P06 is complete only when all of these pass:

```bash
python <skill-dir>/scripts/check_colors.py apps/desktop/angular/src --tokens apps/desktop/angular/src/styles/_tokens.scss
```

- The color check above reports no literal outside the token file.
- All text and control contrast meets the rules above.
- Focus is visible on every control and tab order is logical.
- Reduced motion removes nonessential animation.
- Layouts hold at the minimum window size and at 200% zoom.

## Avoid

Neon, glow, glassmorphism, heavy shadows, noisy textures, thick borders, scanlines, sprites and
characters, fantasy ornaments, fictional data on real screens, and any change to the palette.
