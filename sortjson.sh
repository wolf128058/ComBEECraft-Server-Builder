#!/usr/bin/env bash
set -euo pipefail

tmpFile="$(mktemp)"
trap 'rm -f "$tmpFile"' EXIT

jq '
  def maybe_obj($k; $v):
    if $v == null then {} else {($k): $v} end;

  def ordered_hashes($h):
    (if ($h | has("sha512")) then {sha512: $h.sha512} else {} end)
    + (if ($h | has("sha1")) then {sha1: $h.sha1} else {} end)
    + ($h | del(.sha512, .sha1));

  def ordered_env($e):
    (if ($e | has("client")) then {client: $e.client} else {} end)
    + (if ($e | has("server")) then {server: $e.server} else {} end)
    + ($e | del(.client, .server));

  def ordered_file:
    (.hashes // {}) as $h
    | (.env // {}) as $e
    | {path: .path}
      + (if (.hashes? != null) then {hashes: ordered_hashes($h)} else {} end)
      + (if (.env? != null) then {env: ordered_env($e)} else {} end)
      + maybe_obj("downloads"; .downloads)
      + maybe_obj("fileSize"; .fileSize)
      + (del(.path, .hashes, .env, .downloads, .fileSize));

  def ordered_deps($d):
    (if ($d | has("neoforge")) then {neoforge: $d.neoforge} else {} end)
    + (if ($d | has("minecraft")) then {minecraft: $d.minecraft} else {} end)
    + ($d | del(.neoforge, .minecraft));

  . as $r
  | {
      game: $r.game,
      formatVersion: $r.formatVersion,
      versionId: $r.versionId,
      name: $r.name,
      summary: $r.summary,
      files: (($r.files // []) | sort_by(.path) | map(ordered_file)),
      dependencies: (if ($r | has("dependencies")) then ordered_deps(($r.dependencies // {})) else {} end)
    }
    + ($r | del(.game, .formatVersion, .versionId, .name, .summary, .files, .dependencies))
' modrinth.index.json > "$tmpFile" && mv "$tmpFile" modrinth.index.json

jq --sort-keys 'sort_by(.id)' curseforge.index.json > "$tmpFile" && mv "$tmpFile" curseforge.index.json
