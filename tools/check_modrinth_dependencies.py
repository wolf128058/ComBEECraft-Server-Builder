#!/usr/bin/env python3
"""Check and optionally repair required dependencies in modrinth.index.json.

Outputs:
1) Whether all required dependencies are satisfied.
2) Which required dependencies are missing/unmet.
3) Which mods declare no dependencies (independent update candidates).

Optional fix mode:
- Add missing required dependency mods to the index.
- Align mismatched dependency versions to required exact versions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_VERSION_URL_RE = re.compile(r"/data/([^/]+)/versions/([^/]+)/")
API_BASE = "https://api.modrinth.com/v2"


@dataclass(frozen=True)
class ModFile:
    path: str
    project_id: str
    version_id: str
    file_index: int


@dataclass(frozen=True)
class RequiredDepIssue:
    consumer_mod_path: str
    dep_project_id: str | None
    dep_version_id: str | None
    display: str
    detail: str


def fetch_json(url: str, timeout: float, retries: int = 4) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "ComBEECraft-DepCheck/1.1"})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(min(8.0, 0.6 * (2**attempt)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(5.0, 0.5 * (2**attempt)))
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("unreachable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check required dependencies for mods listed in modrinth.index.json"
    )
    parser.add_argument("--index", default="modrinth.index.json", help="Path to modrinth.index.json")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout per request")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Deprecated compatibility flag; no longer used for API fan-out",
    )
    parser.add_argument("--batch-size", type=int, default=80, help="IDs per batch request")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.15,
        help="Delay in seconds between batch requests (429 mitigation)",
    )
    parser.add_argument("--required-only", action="store_true", help="Only show required deps in tree")
    parser.add_argument("--per-mod-tree", action="store_true", help="Show tree per mod instead of global")

    parser.add_argument(
        "--fix-index",
        action="store_true",
        help="Repair unmet required dependencies directly in index JSON",
    )
    parser.add_argument(
        "--write-index",
        default="",
        help="Output path for repaired index (default: overwrite --index)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak timestamp backup when overwriting the original index",
    )
    parser.add_argument(
        "--allow-downgrade-deps",
        action="store_true",
        help="Allow fix mode to downgrade already-installed dependency mods if exact required version is older",
    )
    return parser.parse_args()


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ERROR: index file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def parse_mod_files(index_json: dict[str, Any]) -> list[ModFile]:
    mods: list[ModFile] = []
    files = index_json.get("files", [])
    for idx, entry in enumerate(files):
        path = entry.get("path", "")
        if not isinstance(path, str) or not path.startswith("mods/"):
            continue

        downloads = entry.get("downloads", [])
        if not downloads or not isinstance(downloads[0], str):
            continue

        match = DATA_VERSION_URL_RE.search(downloads[0])
        if not match:
            continue

        mods.append(
            ModFile(
                path=path,
                project_id=match.group(1),
                version_id=match.group(2),
                file_index=idx,
            )
        )
    return mods


def collect_versions_by_ids(
    version_ids: set[str], timeout: float, batch_size: int, request_delay: float
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    data: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    ids = sorted(version_ids)

    size = max(1, batch_size)
    for i in range(0, len(ids), size):
        batch = ids[i : i + size]
        qs = urllib.parse.urlencode({"ids": json.dumps(batch)})
        url = f"{API_BASE}/versions?{qs}"
        try:
            items = fetch_json(url, timeout)
            if not isinstance(items, list):
                errors.append(f"versions batch starting at {i}: unexpected response type")
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    data[item["id"]] = item
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            errors.append(f"versions batch starting at {i}: {exc}")
            for vid in batch:
                try:
                    item = fetch_json(f"{API_BASE}/version/{vid}", timeout)
                    if isinstance(item, dict):
                        data[vid] = item
                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as item_exc:
                    errors.append(f"version/{vid}: {item_exc}")

        if request_delay > 0:
            time.sleep(request_delay)

    return data, errors


def collect_project_slugs(
    project_ids: set[str], timeout: float, batch_size: int, request_delay: float
) -> tuple[dict[str, str], list[str]]:
    slugs: dict[str, str] = {}
    errors: list[str] = []
    ids = sorted(project_ids)

    if not ids:
        return slugs, errors

    size = max(1, batch_size)
    for i in range(0, len(ids), size):
        batch = ids[i : i + size]
        qs = urllib.parse.urlencode({"ids": json.dumps(batch)})
        url = f"{API_BASE}/projects?{qs}"
        try:
            items = fetch_json(url, timeout)
            if not isinstance(items, list):
                errors.append(f"projects batch starting at {i}: unexpected response type")
                continue
            for item in items:
                if isinstance(item, dict):
                    pid = item.get("id")
                    slug = item.get("slug")
                    if isinstance(pid, str) and isinstance(slug, str):
                        slugs[pid] = slug
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            errors.append(f"projects batch starting at {i}: {exc}")
            for pid in batch:
                try:
                    item = fetch_json(f"{API_BASE}/project/{pid}", timeout)
                    if isinstance(item, dict) and isinstance(item.get("slug"), str):
                        slugs[pid] = item["slug"]
                    else:
                        errors.append(f"project/{pid}: missing slug")
                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as item_exc:
                    errors.append(f"project/{pid}: {item_exc}")

        if request_delay > 0:
            time.sleep(request_delay)

    return slugs, errors


def infer_loaders(runtime_keys: set[str]) -> list[str]:
    known = ["neoforge", "forge", "fabric", "quilt"]
    out = [k for k in known if k in runtime_keys]
    return out or ["neoforge"]


def select_primary_file(version_obj: dict[str, Any]) -> dict[str, Any] | None:
    files = version_obj.get("files", []) or []
    if not isinstance(files, list) or not files:
        return None
    for f in files:
        if isinstance(f, dict) and f.get("primary"):
            return f
    for f in files:
        if isinstance(f, dict):
            return f
    return None


def index_file_from_version(version_obj: dict[str, Any]) -> dict[str, Any] | None:
    file_obj = select_primary_file(version_obj)
    if not file_obj:
        return None

    filename = file_obj.get("filename")
    url = file_obj.get("url")
    hashes = file_obj.get("hashes", {})
    size = file_obj.get("size", 0)

    if not isinstance(filename, str) or not isinstance(url, str):
        return None

    return {
        "path": f"mods/{filename}",
        "hashes": {
            "sha512": str(hashes.get("sha512", "")),
            "sha1": str(hashes.get("sha1", "")),
        },
        "env": {"client": "required", "server": "required"},
        "downloads": [url],
        "fileSize": int(size) if isinstance(size, int) else 0,
    }


def fetch_latest_compatible_version(
    project_id: str,
    timeout: float,
    game_version: str | None,
    loaders: list[str],
) -> dict[str, Any] | None:
    params: dict[str, str] = {}
    if loaders:
        params["loaders"] = json.dumps(loaders)
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}/project/{project_id}/version"
    if qs:
        url = f"{url}?{qs}"

    items = fetch_json(url, timeout)
    if isinstance(items, list) and items:
        for item in items:
            if isinstance(item, dict):
                return item
    return None


def ordered_hashes(hashes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "sha512" in hashes:
        out["sha512"] = hashes["sha512"]
    if "sha1" in hashes:
        out["sha1"] = hashes["sha1"]
    for k, v in hashes.items():
        if k not in {"sha512", "sha1"}:
            out[k] = v
    return out


def ordered_env(env: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "client" in env:
        out["client"] = env["client"]
    if "server" in env:
        out["server"] = env["server"]
    for k, v in env.items():
        if k not in {"client", "server"}:
            out[k] = v
    return out


def ordered_file_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["path"] = entry.get("path")

    if entry.get("hashes") is not None:
        hashes = entry.get("hashes", {})
        if isinstance(hashes, dict):
            out["hashes"] = ordered_hashes(hashes)
        else:
            out["hashes"] = hashes

    if entry.get("env") is not None:
        env = entry.get("env", {})
        if isinstance(env, dict):
            out["env"] = ordered_env(env)
        else:
            out["env"] = env

    if "downloads" in entry:
        out["downloads"] = entry.get("downloads")
    if "fileSize" in entry:
        out["fileSize"] = entry.get("fileSize")

    for k, v in entry.items():
        if k not in {"path", "hashes", "env", "downloads", "fileSize"}:
            out[k] = v

    return out


def ordered_dependencies(deps: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "neoforge" in deps:
        out["neoforge"] = deps["neoforge"]
    if "minecraft" in deps:
        out["minecraft"] = deps["minecraft"]
    for k, v in deps.items():
        if k not in {"neoforge", "minecraft"}:
            out[k] = v
    return out


def normalize_modrinth_index(index_json: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ["game", "formatVersion", "versionId", "name", "summary"]:
        result[key] = index_json.get(key)

    files = index_json.get("files", [])
    if isinstance(files, list):
        sorted_files = sorted(files, key=lambda e: str(e.get("path", "")))
        normalized_files: list[Any] = []
        for entry in sorted_files:
            if isinstance(entry, dict):
                normalized_files.append(ordered_file_entry(entry))
            else:
                normalized_files.append(entry)
        result["files"] = normalized_files
    else:
        result["files"] = []

    deps = index_json.get("dependencies", {})
    if isinstance(deps, dict):
        result["dependencies"] = ordered_dependencies(deps)
    else:
        result["dependencies"] = {}

    for k, v in index_json.items():
        if k not in {"game", "formatVersion", "versionId", "name", "summary", "files", "dependencies"}:
            result[k] = v

    return result


def main() -> None:
    args = parse_args()
    index_path = Path(args.index)
    index_json = load_json_file(index_path)
    mod_files = parse_mod_files(index_json)
    if not mod_files:
        print("No mods with Modrinth version URLs found in index.")
        raise SystemExit(1)

    runtime_dependencies = {
        k: str(v) for k, v in (index_json.get("dependencies", {}) or {}).items() if isinstance(k, str)
    }
    runtime_keys = set(runtime_dependencies.keys())

    version_data, version_errors = collect_versions_by_ids(
        {m.version_id for m in mod_files},
        args.timeout,
        args.batch_size,
        args.request_delay,
    )
    if not version_data:
        print("ERROR: could not fetch any Modrinth version metadata.", file=sys.stderr)
        for err in version_errors:
            print(f"  - {err}", file=sys.stderr)
        raise SystemExit(2)

    installed_projects: dict[str, set[str]] = {}
    installed_version_ids: set[str] = set()
    project_to_entries: dict[str, list[ModFile]] = {}

    for mod in mod_files:
        installed_projects.setdefault(mod.project_id, set()).add(mod.version_id)
        installed_version_ids.add(mod.version_id)
        project_to_entries.setdefault(mod.project_id, []).append(mod)

    # Also trust API payload if available (defensive redundancy)
    for mod in mod_files:
        data = version_data.get(mod.version_id)
        if not data:
            continue
        pid = data.get("project_id")
        if isinstance(pid, str):
            installed_projects.setdefault(pid, set()).add(mod.version_id)

    # Collect external required deps for slug resolution only when needed.
    external_required_project_ids: set[str] = set()
    for mod in mod_files:
        data = version_data.get(mod.version_id, {})
        for dep in data.get("dependencies", []) or []:
            if dep.get("dependency_type") != "required":
                continue
            pid = dep.get("project_id")
            if isinstance(pid, str) and pid not in installed_projects:
                external_required_project_ids.add(pid)

    project_slug_map, project_errors = collect_project_slugs(
        external_required_project_ids,
        args.timeout,
        args.batch_size,
        args.request_delay,
    )

    no_dependency_mods: list[str] = []
    unmet_required: list[RequiredDepIssue] = []
    total_required = 0

    print(f"Dependency report for: {index_path}")
    print(f"Mods checked: {len(mod_files)}")
    if runtime_dependencies:
        dep_kv = ", ".join(f"{k}={v}" for k, v in sorted(runtime_dependencies.items()))
        print(f"Runtime deps from index: {dep_kv}")
    else:
        print("Runtime deps from index: (none)")
    if not args.required_only:
        print("Dependency scope: required + optional + incompatible")
    else:
        print("Dependency scope: required only")
    print("")

    global_tree: dict[str, list[tuple[str, str, str, str]]] = {}
    per_mod_rows: list[tuple[str, list[tuple[str, str, str, str]]]] = []

    for mod in sorted(mod_files, key=lambda m: m.path.lower()):
        data = version_data.get(mod.version_id)
        if not data:
            per_mod_rows.append((mod.path, [("error", "ERROR", "", "could not resolve Modrinth version metadata")]))
            continue

        deps = data.get("dependencies", []) or []
        if not deps:
            no_dependency_mods.append(mod.path)

        mod_rows: list[tuple[str, str, str, str]] = []
        for dep in deps:
            dep_type = str(dep.get("dependency_type", "unknown"))
            if args.required_only and dep_type != "required":
                continue

            dep_pid = dep.get("project_id")
            dep_vid = dep.get("version_id")
            dep_file = dep.get("file_name")

            display = dep_pid or dep_vid or dep_file or "<unknown dependency>"
            if dep_pid and dep_pid in project_slug_map:
                display = f"{dep_pid} ({project_slug_map[dep_pid]})"

            state = "OK"
            detail = ""
            provided = False

            if dep_pid and dep_pid in installed_projects:
                if dep_vid and dep_vid not in installed_projects[dep_pid]:
                    detail = (
                        f"requires exact version {dep_vid}, installed versions: "
                        + ", ".join(sorted(installed_projects[dep_pid]))
                    )
                else:
                    provided = True
                    detail = "provided by mod in pack"
            elif dep_pid and dep_pid in project_slug_map and project_slug_map[dep_pid] in runtime_keys:
                provided = True
                detail = f"provided by runtime: {project_slug_map[dep_pid]}"
            elif dep_vid and dep_vid in installed_version_ids:
                provided = True
                detail = "provided by exact version in pack"
            else:
                if dep_pid and dep_pid in project_slug_map:
                    detail = f"not present in pack/runtime (slug={project_slug_map[dep_pid]})"
                elif dep_pid:
                    detail = "not present in pack/runtime"
                else:
                    detail = "cannot resolve dependency target"

            if provided:
                state = "OK"
            elif dep_type == "required":
                state = "MISSING"
            elif dep_type == "optional":
                state = "OPTIONAL"
            elif dep_type == "incompatible":
                state = "INCOMPATIBLE"
            else:
                state = "UNMET"

            mod_rows.append((dep_type, state, display, detail))
            global_tree.setdefault(display, []).append((mod.path, state, dep_type, detail))

            if dep_type == "required":
                total_required += 1
                if state != "OK":
                    unmet_required.append(
                        RequiredDepIssue(
                            consumer_mod_path=mod.path,
                            dep_project_id=dep_pid if isinstance(dep_pid, str) else None,
                            dep_version_id=dep_vid if isinstance(dep_vid, str) else None,
                            display=display,
                            detail=detail,
                        )
                    )

        per_mod_rows.append((mod.path, mod_rows))

    if args.per_mod_tree:
        print("Dependency tree (per mod)")
        for mod_path, rows in per_mod_rows:
            print(f"- {mod_path}")
            if not rows:
                print("  - (no dependencies)")
                continue
            for dep_type, state, display, detail in rows:
                if dep_type == "error":
                    print(f"  - [ERROR] {detail}")
                else:
                    print(f"  - [{state}] {dep_type}: {display} -> {detail}")
    else:
        print("Dependency tree (global)")
        sorted_deps = sorted(
            global_tree.items(),
            key=lambda item: (
                -sum(1 for _m, state, dep_type, _d in item[1] if dep_type == "required" and state != "OK"),
                -sum(1 for _m, state, _t, _d in item[1] if state != "OK"),
                item[0].lower(),
            ),
        )
        for dep_label, consumers in sorted_deps:
            missing = sum(1 for _m, state, _t, _d in consumers if state != "OK")
            required_count = sum(1 for _m, _s, dep_type, _d in consumers if dep_type == "required")
            print(f"- {dep_label} (used by {len(consumers)} mods, required={required_count}, missing={missing})")
            for mod_path, state, dep_type, detail in sorted(consumers, key=lambda x: (x[1] != "MISSING", x[0].lower())):
                print(f"  - [{state}] {mod_path} ({dep_type}) -> {detail}")

    print("")
    print("Summary")
    print(f"- Required dependencies checked: {total_required}")
    print(f"- Unmet required dependencies: {len(unmet_required)}")
    print(f"- Mods with no dependencies: {len(no_dependency_mods)}")
    print(f"- All required dependencies satisfied: {'YES' if not unmet_required else 'NO'}")

    if unmet_required:
        print("")
        print("Unmet required dependencies")
        for issue in unmet_required:
            print(f"- {issue.consumer_mod_path}: {issue.display} ({issue.detail})")

    print("")
    print("Independent update candidates (mods with no dependencies)")
    for mod_path in sorted(no_dependency_mods, key=str.lower):
        print(f"- {mod_path}")

    # Optional repair mode for required dependencies.
    if args.fix_index:
        print("")
        print("Repair mode")
        if not unmet_required:
            print("- No repairs needed.")
        else:
            files = index_json.get("files", [])
            if not isinstance(files, list):
                print("- ERROR: index JSON has invalid files[] structure; cannot repair.")
            else:
                issues_by_project: dict[str, list[RequiredDepIssue]] = {}
                unresolved_without_project = 0
                for issue in unmet_required:
                    if not issue.dep_project_id:
                        unresolved_without_project += 1
                        continue
                    issues_by_project.setdefault(issue.dep_project_id, []).append(issue)

                loaders = infer_loaders(runtime_keys)
                mc_version = runtime_dependencies.get("minecraft")

                cache_by_version = dict(version_data)
                changes: list[str] = []
                skipped: list[str] = []

                for dep_pid, issues in sorted(issues_by_project.items()):
                    required_vids = sorted({i.dep_version_id for i in issues if i.dep_version_id})
                    if len(required_vids) > 1:
                        skipped.append(
                            f"{dep_pid}: conflicting required versions {', '.join(required_vids)}"
                        )
                        continue

                    target_version_obj: dict[str, Any] | None = None
                    target_vid: str | None = required_vids[0] if required_vids else None

                    if target_vid:
                        if target_vid in cache_by_version:
                            target_version_obj = cache_by_version[target_vid]
                        else:
                            try:
                                obj = fetch_json(f"{API_BASE}/version/{target_vid}", args.timeout)
                                if isinstance(obj, dict):
                                    target_version_obj = obj
                                    cache_by_version[target_vid] = obj
                            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                                skipped.append(f"{dep_pid}: cannot fetch required version {target_vid} ({exc})")
                                continue
                    else:
                        try:
                            target_version_obj = fetch_latest_compatible_version(
                                dep_pid,
                                args.timeout,
                                mc_version,
                                loaders,
                            )
                        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                            skipped.append(f"{dep_pid}: cannot fetch compatible version ({exc})")
                            continue
                        if target_version_obj:
                            tv_id = target_version_obj.get("id")
                            if isinstance(tv_id, str):
                                target_vid = tv_id
                                cache_by_version[tv_id] = target_version_obj

                    if not target_version_obj or not target_vid:
                        skipped.append(f"{dep_pid}: no target version resolved")
                        continue

                    new_entry = index_file_from_version(target_version_obj)
                    if not new_entry:
                        skipped.append(f"{dep_pid}: target version {target_vid} has no usable file metadata")
                        continue

                    existing_entries = project_to_entries.get(dep_pid, [])
                    if existing_entries:
                        first = sorted(existing_entries, key=lambda m: m.file_index)[0]
                        current_vid = first.version_id
                        if current_vid != target_vid and not args.allow_downgrade_deps:
                            current_obj = cache_by_version.get(current_vid)
                            if current_obj is None:
                                try:
                                    fetched_current = fetch_json(
                                        f"{API_BASE}/version/{current_vid}", args.timeout
                                    )
                                    if isinstance(fetched_current, dict):
                                        current_obj = fetched_current
                                        cache_by_version[current_vid] = fetched_current
                                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                                    current_obj = None

                            current_date = (
                                current_obj.get("date_published")
                                if isinstance(current_obj, dict)
                                else None
                            )
                            target_date = target_version_obj.get("date_published")
                            if (
                                isinstance(current_date, str)
                                and isinstance(target_date, str)
                                and target_date < current_date
                            ):
                                skipped.append(
                                    f"{dep_pid}: downgrade blocked ({current_vid} -> {target_vid}); use --allow-downgrade-deps to force"
                                )
                                continue

                        old = files[first.file_index]
                        old_path = old.get("path", f"mods/unknown-{dep_pid}.jar")
                        files[first.file_index] = new_entry
                        changes.append(
                            f"updated {dep_pid}: {old_path} -> {new_entry['path']} (version {target_vid})"
                        )
                    else:
                        files.append(new_entry)
                        changes.append(f"added {dep_pid}: {new_entry['path']} (version {target_vid})")

                if unresolved_without_project:
                    skipped.append(f"{unresolved_without_project} dependency entries without project_id")

                normalized = normalize_modrinth_index(index_json)
                out_path = Path(args.write_index) if args.write_index else index_path
                if out_path.resolve() == index_path.resolve() and not args.no_backup:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    backup_path = index_path.with_suffix(index_path.suffix + f".{stamp}.bak")
                    backup_path.write_text(
                        json.dumps(load_json_file(index_path), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    print(f"- Backup created: {backup_path}")

                out_path.write_text(
                    json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                print(f"- Index updated: {out_path}")
                print(f"- Changes applied: {len(changes)}")
                for c in changes:
                    print(f"  - {c}")

                if skipped:
                    print(f"- Skipped items: {len(skipped)}")
                    for s in skipped:
                        print(f"  - {s}")

    if version_errors or project_errors:
        print("")
        print("Warnings")
        for err in sorted(version_errors):
            print(f"- {err}")
        for err in sorted(project_errors):
            print(f"- {err}")


if __name__ == "__main__":
    main()
