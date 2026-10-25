"""Draft descriptions for a few pending Backfill cards and write an HTML review page.

Runs the same path the Backfill draft route uses (facts, up to 3 Drive originals,
the card's mockup) so the owner can judge Claude's output before the UI ships.
Calls the real Claude API (costs money) and writes nothing to the database.

    poetry run python scripts/describe_check.py OUT.html BC25181 BC251721 ...
"""

from __future__ import annotations

import html
import sys

from mockup_generator.db import product_descriptions_repo as repo
from mockup_generator.describe.draft import MAX_ORIGINALS, draft_description, prepare_image
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.supabase_client import service_client


def _originals(producturl: str | None) -> list[bytes]:
    fid = drive_client.extract_folder_id(producturl)
    if not fid:
        return []
    groups = drive_client.list_folder_image_groups(fid)
    imgs = groups["loose"] + [i for g in groups["groups"] for i in g["images"]]
    return [drive_client.download_file(i["id"]) for i in imgs[:MAX_ORIGINALS]]


def _thumb(raw: bytes) -> str:
    return f'<img src="data:image/jpeg;base64,{prepare_image(raw)}" style="height:160px;margin:2px">'


def main(out: str, pids: list[str]) -> None:
    db = service_client()
    sections = []
    for pid in pids:
        facts = repo.get_facts(db, pid)
        card = (db.table("backfill_items").select("file_id").eq("productid", pid)
                .eq("status", "pending").limit(1).execute().data)
        url = (db.table("products").select("producturl").eq("productid", pid).execute().data or [{}])[0]
        originals = _originals(url.get("producturl"))
        mockup = drive_client.download_file(card[0]["file_id"]) if card else None
        try:
            d = draft_description(facts, originals, mockup)
            draft = (f"<pre>{html.escape(d.description)}</pre>"
                     f"<p><b>Uncertain:</b> {html.escape('; '.join(d.uncertain) or '-')}<br>"
                     f"<b>Conflicts:</b> {html.escape('; '.join(d.conflicts) or '-')}</p>")
        except Exception as exc:  # noqa: BLE001 - report every product, keep going
            draft = f"<p style='color:red'>Draft failed: {html.escape(str(exc))}</p>"
        words = len(d.description.split()) if "d" in locals() else 0
        sections.append(
            f"<section><h2>{pid} · {html.escape(facts.name)} · {html.escape(facts.category_name or '')}</h2>"
            f"<div>{''.join(_thumb(r) for r in originals)}{_thumb(mockup) if mockup else ''}</div>"
            f"<h3>Current</h3><pre>{html.escape(facts.description or '(none)')}</pre>"
            f"<h3>Claude draft ({words} words)</h3>{draft}</section>")
        print(pid, "done", flush=True)
    with open(out, "w") as f:
        f.write("<!doctype html><meta charset=utf-8><title>Description check</title>"
                "<body style='font-family:sans-serif;max-width:900px;margin:auto'>"
                + "<hr>".join(sections) + "</body>")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
