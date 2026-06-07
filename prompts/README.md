# Prompts

Готовые промпты для агента. Референс: [ostrowsky/spec-first-bootstrap](https://github.com/ostrowsky/spec-first-bootstrap/tree/master/prompts).

## Этот проект (brownfield)

```
Read AGENTS.md and docs/specs/README.md in this project first.

This is a brownfield Upwork AI agent project.
Use docs/specs/product-map.md and docs/specs/spec-backlog.md.
Implement only the feature spec I name; update that spec in the same task.
Do not change behavior without updating the matching spec under docs/specs/features/.
```

## Day-to-day

```
Feature: <name>
Spec: docs/specs/features/<name>.md

1. Read the spec.
2. List gaps vs current code.
3. Update spec if the goal changed.
4. Implement.
5. Update Verification mapping in the spec.
```

## Внешние промпты bootstrap

- `brownfield-discovery.md` — карта продукта (уже сделано: `docs/specs/product-map.md`)
- `generate-first-specs.md` — первые спеки (сделано: `docs/specs/features/`)
- `optional-web-qa.md` — когда появится `qa/web/`
