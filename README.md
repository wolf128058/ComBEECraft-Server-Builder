# ComBEECraft Server Builder

Build and maintain the server-side pack definition for ComBEECraft. This repo
keeps the Modrinth/CurseForge indexes and the overrides folder in sync so the
server pack can be assembled consistently.

## Contents

- [`modrinth.index.json`](modrinth.index.json) - Modrinth pack index (mods, datapacks, resourcepacks).
- [`curseforge.index.json`](curseforge.index.json) - CurseForge mod list (used for override downloads).
- [`overrides/`](overrides/) - files that must be shipped as-is (configs, mods, packs).
- [`build/`](build/) - packaged server artifacts (.mrpack) for distribution.
- [`tools/create_prs.py`](tools/create_prs.py) - helper for generating PRs from pack updates.
- [`sortjson.sh`](sortjson.sh) - normalize and sort both index files.
- [`filter.sh`](filter.sh) - remove unsupported mods from Modrinth index via Modrinth API.
- [`curseforge-overrides.sh`](curseforge-overrides.sh) - refresh CurseForge downloads in `overrides/`.

## Requirements

- Bash
- Git
- Python 3.10+ (for `tools/create_prs.py`)
- `requests` (Python package used by `tools/create_prs.py`)
- `jq`
- `curl` (for Modrinth/CurseForge API calls)

## Common Tasks

Normalize/sort JSON indexes:

```bash
./sortjson.sh
```

Remove unsupported entries from the Modrinth index:

```bash
./filter.sh --filter client
# or
./filter.sh --filter server
```

Update CurseForge overrides:

Create a `.env` file with:

```bash
CF_APIKEY=your_api_key
CF_GAMEVERSION=1.21.1
CF_MODLOADER=1
```

Then run:

```bash
./curseforge-overrides.sh
```

Generate update PRs from Modrinth (one branch per mod):

```bash
python3 tools/create_prs.py
```

Optional environment variables for `tools/create_prs.py`:

```bash
DRY_RUN=true
GITHUB_TOKEN=your_token
GITHUB_REPOSITORY=owner/repo
```

## Notes

- For Client Version see: [ComBEECraft-Client-Builder](https://github.com/wolf128058/ComBEECraft-Client-Builder)
- The scripts modify the JSON files in place; commit the results after running.
- `filter.sh` and `curseforge-overrides.sh` hit external APIs and require network
  access plus valid credentials where noted.
- `tools/create_prs.py` talks to the Modrinth and GitHub APIs and requires
  network access and a valid `GITHUB_TOKEN` when creating PRs.
