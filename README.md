# Eukaryota Sync

Automatically builds a taxonomic species note system in Obsidian from your iNaturalist observations, eBird checklists, and a simple text list of anything else you've seen.

No AI required. All data comes from free public APIs.

---

## What it creates

For every species you've recorded, the tool creates an Obsidian note with:
- Full taxonomy in the frontmatter (kingdom → species)
- A photo pulled from Wikipedia (or iNaturalist as fallback)
- A log of every sighting with location, date, and a link to the source record

Notes are organised into folders that mirror the taxonomic hierarchy:

```
Eukaryota/
└── Animalia/
    └── Chordata/
        └── Aves/
            └── Falconiformes/
                └── Falconidae/
                    └── Falco tinnunculus.md
```

Intermediate ranks (family, order, class, etc.) each get their own stub note automatically.

---

## Prerequisites

- [Python 3.8+](https://www.python.org/downloads/)
- An [iNaturalist](https://www.inaturalist.org) account (free)
- An [eBird](https://ebird.org) account if you want to sync bird checklists (free)
- An Obsidian vault (the folder can be empty to start)

---

## Setup

**1. Download the tool**

Download or clone this repository to somewhere on your computer, e.g. `C:\Tools\eukaryota-sync\`.

**2. Install dependencies**

Open a terminal in the folder:
- **Windows 11**: right-click the folder in Explorer → **Open in Terminal**
- **Windows 10**: hold Shift and right-click the folder → **Open PowerShell window here**
- **Or**: open the folder in Explorer, click the address bar at the top, type `cmd`, and press Enter

Then run:

```
pip install -r requirements.txt
```

**3. Create your config file**

Copy `config.example.yml` to `config.yml` and open it in any text editor. Fill in two fields:

```yaml
vault_path: C:\Users\YourName\Documents\MyVault
inaturalist_username: yourname
```

- `vault_path` — the root folder of your Obsidian vault
- `inaturalist_username` — your username from `inaturalist.org/people/yourname`

That's it.

**4. Install the Custom Views plugin**

The card views for browsing your species notes require the Custom Views plugin by Anup Chavan. A pre-configured copy is included in `vault_setup/`.

Copy the `vault_setup/` folder contents into your Obsidian vault root:

```
your-vault/
├── Eukaryota/
│   └── Eukaryota.base           ← copied from vault_setup
└── .obsidian/
    ├── community-plugins.json   ← copied from vault_setup
    └── plugins/
        └── obsidian-custom-views/   ← copied from vault_setup
```

Then open Obsidian, go to **Settings → Community Plugins**, and enable **Custom Views**. If it doesn't appear in the list, click the reload button next to "Installed plugins".

> The plugin is included here for convenience. It was created by [Anup Chavan](https://anupchavan.com).

---

## Usage

Open a terminal in the tool folder (same as Step 2 above) and run one of the following commands.

### Sync iNaturalist observations

```
python sync_species.py inat
```

Pulls all your observations from iNaturalist and creates notes for any species not yet in your vault. On the first run this fetches everything; after that it only pulls new observations since the last sync.

### Sync eBird checklists

```
python sync_species.py ebird
```

Processes your most recent eBird data export. To get the export:

1. Go to [ebird.org/downloadMyData](https://ebird.org/downloadMyData)
2. Download the ZIP file — it will land in your Downloads folder
3. Run the command above

The tool finds the ZIP automatically and won't process the same file twice.

### Add species from a text list

```
python sync_species.py catchup
```

Reads a file called `catchup.txt` from your vault root. One species per line, scientific name first, optional location after a comma:

```
Falco tinnunculus, Tenerife — Jun 2025
Corvus corone, Bristol — Mar 2026
Bufo bufo
# lines starting with # are ignored
```

If a location is given, the note is marked as seen. If no location is given, seen is left blank for you to fill in later. Successfully processed lines are removed from the file automatically; anything that couldn't be resolved stays in for the next run.

### Sync everything at once

```
python sync_species.py all
```

Runs iNat, eBird, and catchup in sequence.

---

## Notes and limitations

**eBird vs iNaturalist taxonomy**
eBird uses the Clements checklist, iNaturalist uses GBIF. A small number of bird species have different scientific names between the two systems. If the tool prints `Could not resolve: Species name`, add that species to `catchup.txt` using the name you can find on [iNaturalist](https://www.inaturalist.org).

**Images**
Photos are downloaded from Wikipedia where available, with iNaturalist as a fallback. If neither source has a suitable image, the tool will tell you the filename to add manually — just drop a JPEG into your vault root with that name and the embed in the note will resolve automatically.

**Above-species records**
Genus- or family-level records (e.g. *Tetragnatha* sp.) are skipped. Add confirmed species to `catchup.txt` once you have an ID.

**iNaturalist username**
The tool uses the public iNaturalist API. Your observations must be set to public (the default) for the sync to work.
