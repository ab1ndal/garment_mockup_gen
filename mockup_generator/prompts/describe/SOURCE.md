# Vendored from retail-inventory

Copied verbatim; do not edit here. Change them in retail-inventory, then re-copy.

| File | Source | Commit |
|------|--------|--------|
| `prompt.md` | `retail-inventory/scripts/describe/prompt.md` | `1d8bda0` |
| `schema.json` | `retail-inventory/scripts/describe/schema.json` | `1d8bda0` |
| `tag_vocab.json` | `retail-inventory/scripts/describe/tag_vocab.json` | `1d8bda0` |
| `approved.json` | `retail-inventory/tasks/description-pilot/approved.json` | `af011eb` |

`describe_prompt.build_prompt` is a port of `retail-inventory/scripts/describe/build_prompt.js`, and
`tag_vocab.py` of `tag_vocab.js` (`familyOf`, `allowed`, `promptBlock`; `test_prompt_block_matches_retail_js`
runs the JS with node to compare).
`tests/test_describe_prompt.py::test_vendored_matches_retail` fails when the copies drift
from a sibling `../retail-inventory` checkout.
