#!/bin/bash

# --- Standardwerte und Konstanten ---
MODRINTH_FILE="modrinth.index.json"
TEMP_FILE="modrinth_temp_unsupported_ids.txt"
FILTER_VALUE="" # Standardmäßig kein Filter

# --- Funktion zur Anzeige der Usage ---
usage() {
    echo "Usage: $0 --filter <client|server>"
    echo ""
    echo "This script filters the '$MODRINTH_FILE' file in place by removing mods"
    echo "that are tagged as 'unsupported' based on the Modrinth API response."
    echo ""
    echo "Options:"
    echo "  --filter <side>    Filtert und MODIFIZIERT die '$MODRINTH_FILE' IN PLACE."
    echo "                     - 'client': Entfernt Einträge, bei denen client_side 'unsupported' ist (laut API)."
    echo "                     - 'server': Entfernt Einträge, bei denen server_side 'unsupported' ist (laut API)."
    exit 1
}

# --- Parameter-Parsing ---
while [ "$#" -gt 0 ]; do
    case "$1" in
        --filter)
            if [ "$2" = "client" ] || [ "$2" = "server" ]; then
                FILTER_VALUE="$2"
                shift 2
            else
                echo "Error: --filter requires 'client' or 'server' as argument." >&2
                usage
            fi
            ;;
        *)
            echo "Error: Unknown parameter '$1'." >&2
            usage
            ;;
    esac
done

# Wenn kein Filter gesetzt ist, gibt es nichts zu tun
if [ -z "$FILTER_VALUE" ]; then
    echo "Error: The --filter parameter is mandatory for this script." >&2
    usage
fi

# --- Hauptlogik zur Bereinigung der Datei ---

echo "Startet die Bereinigung der '$MODRINTH_FILE' für unsupported $FILTER_VALUE-seitige Mods..."

# 1. IDs aus der Datei extrahieren
#    Wir benötigen alle IDs, um sie einzeln zu prüfen.
IDs=$(jq -r '.files[].downloads[0] | split("/") | .[4]' "$MODRINTH_FILE")

# Erzeuge eine leere Datei für die IDs, die entfernt werden müssen
> "$TEMP_FILE"

# 2. Schleife über alle IDs, API abfragen und unsupported IDs sammeln
for ID in $IDs; do
    if [ -z "$ID" ]; then
        continue
    fi
    
    # API abfragen
    API_RESPONSE=$(curl -s "https://api.modrinth.com/v2/project/$ID")

    if [ -z "$API_RESPONSE" ]; then
        echo "Warning: No API response for ID $ID, übersprungen." >&2
        continue
    fi
    
    # Welchen Wert müssen wir aus der API-Antwort extrahieren?
    if [ "$FILTER_VALUE" = "server" ]; then
        SIDE_VALUE=$(echo "$API_RESPONSE" | jq -r '.server_side')
    elif [ "$FILTER_VALUE" = "client" ]; then
        SIDE_VALUE=$(echo "$API_RESPONSE" | jq -r '.client_side')
    fi
    
    # Prüfen, ob der Mod als 'unsupported' markiert ist
    if [ "$SIDE_VALUE" = "unsupported" ]; then
        # Füge die unsupported ID in die temporäre Datei ein
        echo "$ID" >> "$TEMP_FILE"
        MOD_TITLE=$(echo "$API_RESPONSE" | jq -r '.title')
        echo " -> ID '$ID' aka '$MOD_TITLE' wird als unsupported (for $FILTER_VALUE) markiert."
    fi
done

# Prüfen, ob es unsupported IDs gibt
UNSUPPORTED_COUNT=$(wc -l < "$TEMP_FILE")

if [ "$UNSUPPORTED_COUNT" -eq 0 ]; then
    echo "Bereinigung abgeschlossen: Keine Mods als unsupported ($FILTER_VALUE) gefunden. Die Datei wurde nicht verändert."
    rm "$TEMP_FILE"
    exit 0
fi

echo "Es wurden $UNSUPPORTED_COUNT Mod-Einträge zum Entfernen gefunden. Bereinige '$MODRINTH_FILE'..."

# Bereinige die modrinth.index.json mit jq (IN PLACE)

jq --argjson unsupported_ids "$(cat "$TEMP_FILE" | jq -R . | jq -s . | jq 'map(select(length > 0))')" '
    .files |= map(
        select(.downloads[0] | split("/") | .[4] as $id | $unsupported_ids | index($id) | not)
    )
' "$MODRINTH_FILE" > temp.$MODRINTH_FILE && mv temp.$MODRINTH_FILE "$MODRINTH_FILE"

# Aufräumen
rm "$TEMP_FILE"

echo "Bereinigung erfolgreich. Die '$MODRINTH_FILE' wurde modifiziert und $UNSUPPORTED_COUNT Einträge entfernt."