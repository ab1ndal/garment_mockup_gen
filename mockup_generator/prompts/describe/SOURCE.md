# Vendored from retail-inventory

Copied verbatim; do not edit here. Change them in retail-inventory, then re-copy.

| File | Source | Commit |
|------|--------|--------|
| `prompt.md` | `retail-inventory/scripts/describe/prompt.md` | `af011eb` |
| `schema.json` | `retail-inventory/scripts/describe/schema.json` | `af011eb` |
| `approved.json` | `retail-inventory/tasks/description-pilot/approved.json` | `af011eb` |

`describe_prompt.build_prompt` is a port of `retail-inventory/scripts/describe/build_prompt.js`.
`tests/test_describe_prompt.py::test_vendored_matches_retail` fails when the copies drift
from a sibling `../retail-inventory` checkout.
