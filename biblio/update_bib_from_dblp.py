import argparse
import html
import json
import re
import time
from typing import Any, Dict, List, Optional
from tqdm import tqdm

import requests
import bibtexparser


DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"


def normalize_title(text: str) -> str:
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def search_dblp_by_title(title: str, h: int = 25) -> List[Dict[str, Any]]:
    params = {"q": title, "format": "json", "h": str(h)}
    resp = requests.get(DBLP_SEARCH_URL, params=params, timeout=20)
    if resp.status_code != 200:
        return []
    data = resp.json()
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    if isinstance(hits, dict):
        hits = [hits]
    return hits


def choose_best_hit(hits: List[Dict[str, Any]], title: str, year: Optional[str]) -> Optional[Dict[str, Any]]:
    norm_query = normalize_title(title)
    candidates: List[Dict[str, Any]] = []
    for hit in hits:
        info = hit.get("info", {})
        hit_title = info.get("title") or ""
        hit_title = html.unescape(strip_html_tags(hit_title))
        hit_norm = normalize_title(hit_title)
        score = 0
        if hit_norm == norm_query:
            score += 100
        hit_year = str(info.get("year")) if info.get("year") is not None else None
        if year and hit_year and str(year) == hit_year:
            score += 5
        # Prefer DBLP canonical records (heuristic: presence of url in dblp/rec)
        url = info.get("url") or ""
        if url.startswith("https://dblp.org/rec/"):
            score += 1
        candidates.append({"score": score, "hit": hit})

    if not candidates:
        return None
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]["hit"] if candidates[0]["score"] >= 100 else None


def fetch_bib_from_dblp_hit(hit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    info = hit.get("info", {})
    rec_url = info.get("url")
    if not rec_url:
        return None
    if not rec_url.endswith(".bib"):
        rec_url = rec_url + ".bib"
    resp = requests.get(rec_url, timeout=20)
    if resp.status_code != 200:
        return None
    db = bibtexparser.loads(resp.text)
    if not db.entries:
        return None
    return db.entries[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Update BibTeX entries from DBLP while preserving labels.")
    parser.add_argument("--input", "-i", required=True, help="Path to input .bib file")
    parser.add_argument("--output", "-o", help="Path to output .bib (default: overwrite input)")
    parser.add_argument("--log", "-l", default="log.json", help="Path to JSON log for misses")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between DBLP requests (seconds)")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        bib_db = bibtexparser.load(f)

    misses: List[Dict[str, Any]] = []
    updated = 0
    total = len(bib_db.entries)

    for entry in tqdm(bib_db.entries, desc="Processing entries"):
        entry_type = (entry.get("ENTRYTYPE") or "").lower()
        if entry_type not in {"article", "inproceedings"}:
            continue

        key = entry.get("ID")
        title = entry.get("title")
        year = entry.get("year")

        if not title:
            misses.append({"key": key, "entrytype": entry_type, "reason": "no title field"})
            continue

        hits = search_dblp_by_title(re.sub(r"\s+", " ", title))
        best = choose_best_hit(hits, title, str(year) if year else None)

        if not best:
            misses.append({"key": key, "title": title, "year": year, "reason": "no exact title match in DBLP"})
            time.sleep(args.delay)
            continue

        dblp_entry = fetch_bib_from_dblp_hit(best)
        if not dblp_entry:
            misses.append({"key": key, "title": title, "year": year, "reason": "failed to fetch DBLP BibTeX"})
            time.sleep(args.delay)
            continue

        original_key = key
        entry.clear()
        entry.update(dblp_entry)
        entry["ID"] = original_key
        updated += 1

        time.sleep(args.delay)

    out_path = args.output or args.input
    with open(out_path, "w", encoding="utf-8") as f:
        bibtexparser.dump(bib_db, f)

    with open(args.log, "w", encoding="utf-8") as f:
        json.dump({"updated": updated, "total_entries": total, "not_found": misses}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()


