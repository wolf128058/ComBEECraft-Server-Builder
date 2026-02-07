#!/usr/bin/env python3
import os
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List
import requests

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

MODRINTH_INDEX = "modrinth.index.json"
MODRINTH_INDEX_PATH = REPO_ROOT / MODRINTH_INDEX
MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_USER_AGENT = "ComBEECraft-Server-Updater"

ZIEL_BRANCH = "next"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")

# ------------------------------------------------------------
# GIT
# ------------------------------------------------------------

def git(*args):
    subprocess.run(["git", *args], check=True)

# ------------------------------------------------------------
# MODRINTH
# ------------------------------------------------------------

MODRINTH_SESSION = requests.Session()
MODRINTH_SESSION.headers.update({
    "User-Agent": MODRINTH_USER_AGENT,
    "Accept": "application/json",
})

def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _rate_limit_sleep_from_headers(headers: Dict[str, str], min_seconds: int = 1) -> int:
    reset_seconds = _safe_int(headers.get("X-Ratelimit-Reset"), min_seconds)
    return max(reset_seconds, min_seconds)

def modrinth_get(path: str, timeout: int = 30, max_retries: int = 6) -> requests.Response:
    url = f"{MODRINTH_API}{path}"

    for attempt in range(1, max_retries + 1):
        response = MODRINTH_SESSION.get(url, timeout=timeout)
        limit = response.headers.get("X-Ratelimit-Limit")
        remaining = _safe_int(response.headers.get("X-Ratelimit-Remaining"), -1)

        if response.status_code == 429 and attempt < max_retries:
            wait_seconds = _rate_limit_sleep_from_headers(response.headers)
            print(f"⏳ Modrinth rate limit hit ({remaining}/{limit}). Waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()

        if remaining == 0:
            wait_seconds = _rate_limit_sleep_from_headers(response.headers)
            print(f"⏳ Modrinth quota exhausted ({remaining}/{limit}). Waiting {wait_seconds}s for reset...")
            time.sleep(wait_seconds)

        return response

    raise RuntimeError(f"Modrinth request failed after {max_retries} retries: {url}")

def get_slug(project_id: str) -> str:
    r = modrinth_get(f"/project/{project_id}", timeout=30)
    return r.json()["slug"]

def matches_mc_strict(mc: str, game_versions: List[str]) -> bool:
    return mc in game_versions

def fetch_latest_version(project_id: str, mc_version: str, loaders: List[str]) -> Dict:
    r = modrinth_get(f"/project/{project_id}/version", timeout=30)
    versions = r.json()

    def ok(v):
        if not matches_mc_strict(mc_version, v.get("game_versions", [])):
            return False
        vloaders = [l.lower() for l in v.get("loaders", [])]
        return bool(set(vloaders) & set(loaders))

    candidates = [v for v in versions if ok(v)]
    if not candidates:
        raise RuntimeError("No matching versions")

    candidates.sort(key=lambda v: v["date_published"], reverse=True)
    return candidates[0]

# ------------------------------------------------------------
# INDEX HANDLING
# ------------------------------------------------------------

def build_entry(version: Dict, old: Dict) -> Dict:
    file = next(f for f in version["files"] if f.get("primary", False))

    return {
        "path": f"mods/{file['filename']}",
        "hashes": {
            "sha512": file["hashes"]["sha512"],
            "sha1": file["hashes"]["sha1"],
        },
        "env": old["env"],
        "downloads": [file["url"]],
        "fileSize": file["size"],
    }

def entry_changed(old: Dict, new: Dict) -> bool:
    print("    SHA512 old:", old["hashes"]["sha512"])
    print("    SHA512 new:", new["hashes"]["sha512"])
    print("    SHA match :", old["hashes"]["sha512"] == new["hashes"]["sha512"])
    print("    URL match :", old["downloads"][0] == new["downloads"][0])
    print("    PATH match:", old["path"] == new["path"])

    return (
        old["hashes"]["sha512"] != new["hashes"]["sha512"]
        or old["downloads"][0] != new["downloads"][0]
        or old["path"] != new["path"]
    )

def load_index_from_branch(branch_name: str) -> Dict:
    git("fetch", "origin", branch_name)
    git("checkout", branch_name)
    with MODRINTH_INDEX_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    with MODRINTH_INDEX_PATH.open("r", encoding="utf-8") as f:
        index = json.load(f)

    mc_version = index["dependencies"]["minecraft"]
    loaders = [k for k in index["dependencies"] if k != "minecraft"]

    git("checkout", ZIEL_BRANCH)

    for old in index["files"]:
        url = old["downloads"][0]
        if "/data/" not in url:
            continue

        project_id = url.split("/data/")[1].split("/")[0]
        slug = get_slug(project_id)

        print(f"\n🔍 Checking {slug}")

        try:
            latest = fetch_latest_version(project_id, mc_version, loaders)
            new = build_entry(latest, old)

            if not entry_changed(old, new):
                print("⏭  already up-to-date")
                continue

            branch = f"update-{slug}"

            # ➕ Check if branch already exists remotely
            result = subprocess.run(["git", "ls-remote", "--heads", "origin", branch],
                                    capture_output=True, text=True)
            if result.stdout.strip():
                print(f"🔁 Branch {branch} exists remotely. Checking for identical content...")

                # Save current branch
                current_branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"]
                ).decode().strip()

                # Load index from existing branch
                existing_index = load_index_from_branch(branch)
                existing_entry = next(
                    (f for f in existing_index["files"] if f["path"] == new["path"]), None
                )

                # Switch back
                git("checkout", current_branch)

                # If existing entry is same as new, skip
                if existing_entry and not entry_changed(existing_entry, new):
                    print(f"⏭  Skipping: {branch} already has identical change.")
                    continue

            print("✅ UPDATE DETECTED")

            git("checkout", "-B", branch)

            new_index = json.loads(json.dumps(index))
            new_index["files"] = [
                new if f["path"] == old["path"] else f
                for f in index["files"]
            ]

            MODRINTH_INDEX_PATH.write_text(
                json.dumps(new_index, indent=2) + "\n",
                encoding="utf-8",
            )

            git("add", MODRINTH_INDEX)
            git("commit", "-m", f"- update {slug}")

            if DRY_RUN:
                print("🧪 Dry‑run: no push / no PR")
                git("checkout", ZIEL_BRANCH)
                continue

            git("push", "-f", "origin", branch)
            if GITHUB_TOKEN:
                pr_resp = requests.post(
                    f"https://api.github.com/repos/{GITHUB_REPO}/pulls",
                    headers={
                        "Authorization": f"token {GITHUB_TOKEN}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={
                        "title": f"Update {slug}",
                        "head": branch,
                        "base": ZIEL_BRANCH,
                        "body": "Automated Modrinth update",
                    },
                    timeout=30,
                )

                if pr_resp.status_code == 201:
                    print(f"🟢 PR created for {branch}")
                elif pr_resp.status_code == 422 and "A pull request already exists" in pr_resp.text:
                    print(f"⏭  PR already exists for {branch}")
                else:
                    print(f"⚠️ PR creation response for {branch}: {pr_resp.status_code} {pr_resp.text}")

        except Exception as e:
            print(f"⚠️ {slug}: {e}")

    git("checkout", ZIEL_BRANCH)

if __name__ == "__main__":
    main()
