#!/usr/bin/env python3
"""Eukaryota species note sync — iNaturalist, eBird, and catchup list."""

import csv
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path

import requests
import yaml
from PIL import Image

# ── Config & state ───────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yml"
STATE_PATH = ROOT / "sync_state.json"


def load_config():
    # Preprocess path lines before YAML parsing: replace backslashes with
    # forward slashes so YAML doesn't interpret \P, \N etc. as escape sequences.
    path_keys = {"vault_path", "downloads_path"}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    fixed = []
    for line in lines:
        for key in path_keys:
            if re.match(rf'^\s*{key}\s*:', line):
                colon = line.index(":") + 1
                val = line[colon:].strip().strip("\"'")
                val = val.replace("\\", "/")
                line = line[:colon] + f" {val}\n"
                break
        fixed.append(line)
    return yaml.safe_load("".join(fixed))


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"last_inat_sync": None, "processed_ebird": []}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Vault paths ──────────────────────────────────────────────────────────────

def vault(cfg):
    return Path(cfg["vault_path"])


def eukaryota(cfg):
    return vault(cfg) / "Eukaryota"


def species_path(cfg, t):
    return (
        eukaryota(cfg)
        / t["kingdom"] / t["phylum"] / t["class"]
        / t["order"] / t["family"]
        / f"{t['genus']} {t['species']}.md"
    )


RANKS = ["kingdom", "phylum", "class", "order", "family"]


def rank_path(cfg, t, rank):
    base = eukaryota(cfg)
    r = t[rank]
    if rank == "kingdom":
        return base / r / f"{r}.md"
    if rank == "phylum":
        return base / t["kingdom"] / r / f"{r}.md"
    if rank == "class":
        return base / t["kingdom"] / t["phylum"] / r / f"{r}.md"
    if rank == "order":
        return base / t["kingdom"] / t["phylum"] / t["class"] / r / f"{r}.md"
    if rank == "family":
        return base / t["kingdom"] / t["phylum"] / t["class"] / t["order"] / r / f"{r}.md"


# ── iNaturalist API ──────────────────────────────────────────────────────────

INAT = "https://api.inaturalist.org/v1"


def inat_get(endpoint, params=None):
    for attempt in range(3):
        try:
            r = requests.get(f"{INAT}/{endpoint}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2)


def get_observations(username, after_id=None):
    obs, page = [], 1
    params = {
        "user_login": username,
        "per_page": 200,
        "order": "asc",
        "order_by": "id",
    }
    if after_id:
        params["id_above"] = after_id
    while True:
        data = inat_get("observations", {**params, "page": page})
        results = data.get("results", [])
        obs.extend(results)
        if len(obs) >= data.get("total_results", 0) or not results:
            break
        page += 1
        time.sleep(0.5)
    return obs


IUCN_MAP = {
    "LC": "Least Concern",
    "NT": "Near Threatened",
    "VU": "Vulnerable",
    "EN": "Endangered",
    "CR": "Critically Endangered",
    "EW": "Extinct in the Wild",
    "EX": "Extinct",
}


def taxonomy_from_id(taxon_id):
    data = inat_get(f"taxa/{taxon_id}")
    if not data.get("results"):
        return None
    t = data["results"][0]
    ancestors = {a["rank"]: a["name"] for a in t.get("ancestors", [])}
    name_parts = t.get("name", "").split()
    cs = t.get("conservation_status") or {}
    return {
        "kingdom":      ancestors.get("kingdom", ""),
        "phylum":       ancestors.get("phylum", ""),
        "class":        ancestors.get("class", ""),
        "order":        ancestors.get("order", ""),
        "family":       ancestors.get("family", ""),
        "genus":        name_parts[0] if name_parts else "",
        "species":      name_parts[1] if len(name_parts) > 1 else "",
        "common_name":  t.get("preferred_common_name", ""),
        "iucn":         IUCN_MAP.get((cs.get("status") or "").upper(), ""),
        "default_photo": (t.get("default_photo") or {}).get("medium_url", ""),
    }


def taxonomy_by_name(name):
    data = inat_get("taxa", {"q": name, "rank": "species", "per_page": 10})
    for t in data.get("results", []):
        if t["name"].lower() == name.lower():
            return taxonomy_from_id(t["id"])
    return None


def taxonomy_complete(t):
    return t and all(t.get(r) for r in RANKS)


# ── Images ───────────────────────────────────────────────────────────────────

WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary"


def wiki_image_url(name):
    try:
        r = requests.get(f"{WIKI}/{name.replace(' ', '_')}", timeout=10)
        if r.status_code == 200:
            thumb = r.json().get("thumbnail", {}).get("source", "")
            if thumb:
                return re.sub(r"/thumb(/.+)/[^/]+$", r"\1", thumb)
    except Exception:
        pass
    return None


def save_image(url, dest, max_px=800, max_kb=200):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        if max(img.size) > max_px:
            img.thumbnail((max_px, max_px), Image.LANCZOS)
        quality = 85
        while quality > 20:
            buf = BytesIO()
            img.save(buf, "JPEG", quality=quality)
            if buf.tell() <= max_kb * 1024:
                break
            quality -= 10
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(buf.getvalue())
        return True
    except Exception:
        return False


def images_dir(cfg):
    d = vault(cfg) / "images"
    d.mkdir(exist_ok=True)
    return d


def fetch_species_image(cfg, t):
    filename = f"{t['genus']}_{t['species']}.jpg"
    dest = images_dir(cfg) / filename
    if dest.exists():
        return filename
    url = wiki_image_url(f"{t['genus']} {t['species']}") or t.get("default_photo", "")
    if url:
        save_image(url, dest)
    if not dest.exists():
        print(f"  No image for {t['genus']} {t['species']} — add {filename} to images/ manually")
    return filename


def inat_taxon_image(name):
    try:
        data = inat_get("taxa", {"q": name, "per_page": 5})
        for t in data.get("results", []):
            if t["name"].lower() == name.lower():
                return (t.get("default_photo") or {}).get("medium_url", "")
    except Exception:
        pass
    return None


def fetch_rank_image(cfg, rank_name):
    filename = f"{rank_name}.jpg"
    dest = images_dir(cfg) / filename
    if dest.exists():
        return filename
    url = wiki_image_url(rank_name) or inat_taxon_image(rank_name)
    if url:
        save_image(url, dest)
    if not dest.exists():
        print(f"  No image for {rank_name} — add {filename} to vault root manually")
    return filename


# ── Note content ─────────────────────────────────────────────────────────────

NOW = datetime.now().strftime("%Y-%m-%dT%H:%M")


def to_yaml_block(fields):
    lines = ["---"]
    for k, v in fields.items():
        if v is None or v == "":
            lines.append(f"{k}:")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def species_content(t, img, seen, first_location=None):
    fields = {
        "created": NOW, "updated": NOW,
        "class": t["class"],
        "IUCN red list": t["iucn"] or None,
        "tags": ["📝/🌱"],
        "kingdom": t["kingdom"], "phylum": t["phylum"],
        "order": t["order"], "family": t["family"],
        "genus": t["genus"], "species": t["species"],
        "common name": t["common_name"],
        "theme": "nature", "rating": None,
        "seen": True if seen else None,
    }
    body = f"# Info\n\n![[{img}]]\n\n# Locations seen"
    if first_location:
        body += f"\n{first_location}"
    return f"{to_yaml_block(fields)}\n\n{body}\n"


def rank_content(t, rank, img):
    rank_idx = RANKS.index(rank)
    crumbs = ["[[Eukaryota]]"] + [f"[[{t[r]}]]" for r in RANKS[: rank_idx + 1]]
    breadcrumb = " > ".join(crumbs)
    fields = {"created": NOW, "updated": NOW, "tags": ["📝/🌱"], "theme": rank}
    for r in RANKS[: rank_idx + 1]:
        fields[r] = t[r]
    fields["common name"] = ""
    return f"{to_yaml_block(fields)}\n\n{breadcrumb}\n\n# Info\n\n![[{img}]]\n"


# ── Vault writes ─────────────────────────────────────────────────────────────

def write_note(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_sighting(note_path, entry):
    text = note_path.read_text(encoding="utf-8")
    if entry in text:
        return
    note_path.write_text(text.rstrip() + f"\n{entry}\n", encoding="utf-8")


def ensure_eukaryota_root(cfg):
    p = eukaryota(cfg) / "Eukaryota.md"
    if p.exists():
        return
    filename = "Eukaryota.jpg"
    dest = images_dir(cfg) / filename
    if not dest.exists():
        url = wiki_image_url("Eukaryota") or inat_taxon_image("Eukaryota")
        if url:
            save_image(url, dest)
    fields = {
        "created": NOW, "updated": NOW,
        "tags": ["📝/🌱"],
        "theme": "eukaryota",
        "common name": "All Life",
    }
    content = f"{to_yaml_block(fields)}\n\n# Info\n\n![[{filename}]]\n"
    write_note(p, content)
    print("  Created: Eukaryota.md")


def ensure_stubs(cfg, t):
    for rank in RANKS:
        if not t.get(rank):
            continue
        p = rank_path(cfg, t, rank)
        if not p.exists():
            img = fetch_rank_image(cfg, t[rank])
            write_note(p, rank_content(t, rank, img))
            print(f"  Stub: {t[rank]}")


def create_or_sync(cfg, t, entry, seen=True):
    p = species_path(cfg, t)
    if not p.exists():
        ensure_stubs(cfg, t)
        img = fetch_species_image(cfg, t)
        write_note(p, species_content(t, img, seen, first_location=entry))
        return "created"
    if entry:
        append_sighting(p, entry)
    return "synced"


# ── iNat sync ────────────────────────────────────────────────────────────────

def inat_entry(obs):
    place = obs.get("place_guess") or "Unknown"
    date = obs.get("observed_on", "")
    url = f"https://www.inaturalist.org/observations/{obs['id']}"
    grade = obs.get("quality_grade", "")
    return f"[{place} — {date}]({url}) · {grade}"


def run_inat(cfg, state):
    ensure_eukaryota_root(cfg)
    username = cfg["inaturalist_username"]
    after_id = state.get("last_inat_id")
    print(f"iNaturalist: {username}" + (f" (after id {after_id})" if after_id else " (full sync)"))

    obs_list = get_observations(username, after_id)
    print(f"  {len(obs_list)} observations")
    counts = {"created": 0, "synced": 0, "skipped": 0}
    max_id = after_id or 0

    for obs in obs_list:
        max_id = max(max_id, obs.get("id", 0))
        taxon = obs.get("taxon")
        if not taxon or taxon.get("rank") != "species":
            continue
        t = taxonomy_from_id(taxon["id"])
        if not taxonomy_complete(t):
            counts["skipped"] += 1
            continue
        result = create_or_sync(cfg, t, inat_entry(obs), seen=True)
        counts[result] += 1
        if result == "created":
            print(f"  + {t['genus']} {t['species']}")
        time.sleep(0.3)

    if max_id:
        state["last_inat_id"] = max_id
    print(f"  Done — {counts['created']} created, {counts['synced']} synced, {counts['skipped']} skipped")


# ── eBird sync ───────────────────────────────────────────────────────────────

def find_ebird_zip(cfg):
    if "downloads_path" in cfg and cfg["downloads_path"]:
        candidates = [Path(cfg["downloads_path"])]
    else:
        # Try multiple common locations — Path.home() can resolve unexpectedly
        # on some Windows setups
        candidates = [Path.home() / "Downloads"]
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(Path(userprofile) / "Downloads")
    for downloads in candidates:
        zips = sorted(downloads.glob("ebird_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if zips:
            return zips[0]
    return None


def run_ebird(cfg, state):
    ensure_eukaryota_root(cfg)
    zfile = find_ebird_zip(cfg)
    if not zfile:
        print("eBird: no export zip found in Downloads")
        return

    processed = state.get("processed_ebird", [])
    if str(zfile) in processed:
        print(f"eBird: {zfile.name} already processed")
        return

    print(f"eBird: {zfile.name}")

    with zipfile.ZipFile(zfile) as z:
        csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(csv_name) as f:
            content = f.read().decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(content)))
    print(f"  {len(rows)} rows")
    counts = {"created": 0, "synced": 0, "skipped": 0}

    for row in rows:
        sci_name = row.get("Scientific Name", "").strip()
        if not sci_name:
            continue
        # Strip parenthetical annotations: "Columba livia (Feral Pigeon)" → "Columba livia"
        sci_name = re.sub(r'\s*\(.*?\)', '', sci_name).strip()
        # Strip subspecies epithet from trinomials: "Elanus caeruleus caeruleus" → "Elanus caeruleus"
        parts = sci_name.split()
        if len(parts) == 3:
            sci_name = f"{parts[0]} {parts[1]}"
        t = taxonomy_by_name(sci_name)
        if not taxonomy_complete(t):
            if not t:
                print(f"  Could not resolve: {sci_name}")
            counts["skipped"] += 1
            continue
        sub_id = row.get("Submission ID", "")
        location = row.get("Location", "Unknown")
        date = row.get("Date", "")
        count = row.get("Count", "")
        entry = f"[{location} — {date}](https://ebird.org/checklist/{sub_id}) · {count}"
        result = create_or_sync(cfg, t, entry, seen=True)
        counts[result] += 1
        if result == "created":
            print(f"  + {sci_name}")
        time.sleep(0.3)

    processed.append(str(zfile))
    state["processed_ebird"] = processed
    print(f"  Done — {counts['created']} created, {counts['synced']} synced, {counts['skipped']} skipped")


# ── Catchup sync ─────────────────────────────────────────────────────────────

def parse_catchup_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None
    if "," in line:
        name, loc = line.split(",", 1)
        return name.strip(), loc.strip()
    return line, None


def run_catchup(cfg):
    ensure_eukaryota_root(cfg)
    catchup_file = vault(cfg) / "catchup.txt"
    if not catchup_file.exists():
        print("Catchup: no catchup.txt at vault root")
        return

    lines = catchup_file.read_text(encoding="utf-8").splitlines()
    remaining = []
    counts = {"created": 0, "synced": 0, "skipped": 0}

    for line in lines:
        sci_name, location = parse_catchup_line(line)
        if not sci_name:
            remaining.append(line)
            continue
        t = taxonomy_by_name(sci_name)
        if not taxonomy_complete(t):
            if not t:
                print(f"  Could not resolve: {sci_name}")
            remaining.append(line)
            counts["skipped"] += 1
            continue
        result = create_or_sync(cfg, t, location, seen=bool(location))
        counts[result] += 1
        if result == "created":
            print(f"  + {sci_name}")
        time.sleep(0.3)

    catchup_file.write_text(
        "\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8"
    )
    print(f"  Done — {counts['created']} created, {counts['synced']} synced, {counts['skipped']} skipped")


# ── Entry point ──────────────────────────────────────────────────────────────

USAGE = "Usage: python sync_species.py [inat|ebird|catchup|all]"


def main():
    if not CONFIG_PATH.exists():
        sys.exit("Config not found — copy config.example.yml to config.yml and fill in your details.")

    cfg = load_config()
    state = load_state()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inat"

    if cmd not in ("inat", "ebird", "catchup", "all"):
        sys.exit(USAGE)

    if cmd in ("inat", "all"):
        run_inat(cfg, state)
    if cmd in ("ebird", "all"):
        run_ebird(cfg, state)
    if cmd in ("catchup", "all"):
        run_catchup(cfg)

    save_state(state)


if __name__ == "__main__":
    main()
