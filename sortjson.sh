#!/bin/bash

jq '
  .files |= sort_by(.path)
  | .files[].hashes |= {sha512: .sha512, sha1: .sha1}
  | .files[].env    |= {client: .client, server: .server}
' modrinth.index.json > tmp.json && mv tmp.json modrinth.index.json