# Documentation

This documentation describes the **target product** and marks what exists today. Unless a
section says "implemented", treat it as planned. The [roadmap](roadmap.md) holds the exit
checks, and the [assessment](assessment.md) holds the evidence for the current state.

| Document | Purpose |
| --- | --- |
| [Requirements](requirements.md) | What the product must do, its limits and non-goals |
| [Architecture](architecture.md) | Electron shell, containers, data flow, boundaries and the migration map |
| [Pipeline](pipeline.md) | The curation pipeline, stage by stage |
| [AI providers](ai-providers.md) | Local model default, Codex CLI option and the model bake-off |
| [Technology choices](technology.md) | Stack, versions, rationale, alternatives and sources |
| [Operations](operations.md) | `start.sh`, prerequisites, data locations and troubleshooting |
| [Security and privacy](security-privacy.md) | Threat model, trust boundaries and residual risks |
| [Design system](design-system.md) | Minimal Pixel JRPG interface on the existing color tokens |
| [Testing](testing.md) | How to verify, with exact commands and dated results |
| [Roadmap](roadmap.md) | Work packages, dependencies and exit checks |
| [Repository assessment](assessment.md) | What the code does today, verified defects and improvements |
| [Risks](risks.md) | Known problems, assumptions, open questions and blockers |
| [Claude Code](claude-code.md) | Agent configuration, hooks and reviewed skills |
| [Decisions](decisions/) | One ADR per material decision |

## Conventions

- Documents and source comments are written in English.
- Delivered behavior and plans stay separate. A check is reported as passed only after it ran.
- Roadmap, changelog and affected documents change in the same commit as the code.
- Comments state contracts: what a function promises and requires, and how it fails.
