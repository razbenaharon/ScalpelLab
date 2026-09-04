# Public Release Follow-ups

Two decisions remain intentionally unresolved because they depend on research policy and ownership, not code quality.

## 1. Surgical case timing metadata

`docs/case_times.csv` contains timing metadata for real surgical cases. The rest of the repository already treats sensitive research data conservatively, including encrypted database content and removal of generated path inventories.

Before changing this file, confirm the applicable lab / IRB policy and choose one of these approaches:

- keep it public if explicitly permitted;
- protect it with the repository's existing encryption mechanism;
- replace it with a synthetic example and keep the real file private;
- remove it from the public repository if it is operational data rather than documentation.

Do not make this decision based only on the absence of direct patient identifiers.

## 2. Repository license

No open-source license was added during the modernization pass. Before choosing one, confirm who owns the research infrastructure and whether the Technion, lab, collaborators, or another institution has licensing requirements.

Until that is resolved, leaving the repository without an explicit license is more accurate than guessing.
