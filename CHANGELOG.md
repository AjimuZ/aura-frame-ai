# Changelog

## Unreleased

### Changed

- The documentation now describes AuraFrame AI as a general tool for finding beautiful photos of any
  subject. Application behavior has not changed yet.
- The documentation now defines the target: an Electron desktop application whose services run in Docker,
  started with `start.sh`, a comparative curation pipeline, a recommended local model and an optional Codex
  CLI provider. All of this is planned.
- Project instructions and specialist agent definitions now follow the new direction.
- The engine no longer carries the retired single-purpose vocabulary. The model score field is now
  `overall_score` (`overallScore` in the data sent to the desktop app) and the ranking weight is
  `gemma_overall_weight`. The clothing score is now the neutral `style_score` (`styleScore` in the data
  sent to the desktop app, `style` column in the database, `style_context_weight` in the ranking
  configuration); the ranking arithmetic is unchanged. The local and cloud prompts now judge a photograph in general. The cloud flow
  keeps its behavior; only wording, the review field names (`overall_value`, `selection_value`, `appeal`)
  and the prompt versions changed, so cached cloud results are not reused.
- Export folders and report sections use neutral names (`top_06_final_set`, `top_20_best`,
  `top_50_review`). Analysis data from earlier runs must be recreated by a new run; there is no migration
  ([ADR 0001](docs/decisions/0001-general-purpose-photo-curation.md)).
- The desktop app (Electron contracts, Angular models, mock API, dashboard) now uses `overallScore` and
  `styleScore`, matching the engine, and the package description calls the product a local AI photo
  curation tool.

### Added

- `tests/retired-terms.test.cjs`, a guard test that fails, naming file and line, when the retired
  product vocabulary reappears in any tracked or unignored file.
- `scripts/check.sh`, one entry point for lint, builds, Node tests and the containerized engine tests,
  with `--node`, `--engine` and `--integration` subsets and visible SKIP lines.
- Documentation index, requirements, roadmap with exit checks, testing guide with dated results, risks,
  repository assessment with verified defects, Claude Code guide with the approved skills, and nine
  decision records.

### Removed

- All previous documents: the old product specification, architecture, pipeline, design system, cloud
  mode guide, agent workflow, setup guide and portfolio text.
