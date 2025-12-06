#!/bin/bash

# --- Konfigurations- und Dateipfade ---
JSON_FILE="curseforge.index.json"
ENV_FILE=".env"

# --- Fehlerprüfungen ---

if [ ! -f "$ENV_FILE" ]; then
    echo "FEHLER: Die Konfigurationsdatei $ENV_FILE wurde nicht gefunden." >&2
    exit 1
fi

if [ ! -f "$JSON_FILE" ]; then
    echo "FEHLER: Die JSON-Indexdatei $JSON_FILE wurde nicht gefunden." >&2
    exit 1
fi

# --- Umgebungsvariablen laden ---
set -o allexport
source "$ENV_FILE"
set +o allexport

# --- Hauptlogik: Schleife über die Mod-IDs und Pfade ---

echo "Starte Abfragen, Cleanup & Download für alle Mods aus der Datei '$JSON_FILE'..."
echo "--------------------------------------------------------"

# Temporäre Datei für das Speichern von ID und Path (ModID<TAB>Path)
TEMP_MODS_LIST=$(mktemp)
jq -r '.[] | "\(.id)\t\(.path)"' < "$JSON_FILE" > "$TEMP_MODS_LIST"

# Schleife liest die ID und den Path aus der temporären Datei
while IFS=$'\t' read -r MOD_ID TARGET_PATH; do
    
    if [ -z "$MOD_ID" ]; then
        continue
    fi
    
    echo ">> Starte Verarbeitung für Mod ID: $MOD_ID (Zielpfad: $TARGET_PATH)"
    
    TEMP_RESPONSE=$(mktemp)
    
    # Aufbau des CURL-Befehls
    CURL_COMMAND="curl --silent --show-error --request GET \\
      --url 'https://api.curseforge.com/v1/mods/$MOD_ID/files?sort=dateCreated&sortDirection=desc&gameVersion=$CF_GAMEVERSION&modLoaderType=$CF_MODLOADER' \\
      --header 'Accept: application/json' \\
      --header 'x-api-key: $CF_APIKEY'"
   
    # Ausführung des Befehls und Speicherung der reinen JSON-Antwort
    eval "$CURL_COMMAND" > "$TEMP_RESPONSE" 2>/dev/null 
      
    CURL_EXIT_CODE=$?

    if [ $CURL_EXIT_CODE -ne 0 ]; then
        echo "   [FATAL] Curl-Befehl für ID $MOD_ID fehlgeschlagen (Exit Code $CURL_EXIT_CODE)." >&2
        rm -f "$TEMP_RESPONSE"
        continue
    fi
    
    # 1. Extrahieren der Download-URL und des Dateinamens des neuesten Files (für den Download)
    DOWNLOAD_URL=$(jq -r '.data[0].downloadUrl // "FEHLER_BEI_JQ"' < "$TEMP_RESPONSE")
    LATEST_FILENAME=$(jq -r '.data[0].fileName // "FEHLER_BEI_JQ"' < "$TEMP_RESPONSE")

    
    # 2. Prüfen auf gültige Download-Informationen
    if [ "$DOWNLOAD_URL" == "FEHLER_BEI_JQ" ] || [ -z "$DOWNLOAD_URL" ]; then
        echo "   [WARN] Konnte Download-URL/Dateiname nicht extrahieren. Mod wird übersprungen."
        rm -f "$TEMP_RESPONSE"
        continue
    fi
    
    # 3. Cleanup: Liste aller zu löschenden Dateinamen (Alle Dateinamen aus dem .data Array)
    FILES_TO_DELETE=$(jq -r '.data[].fileName // empty' < "$TEMP_RESPONSE")
    
    echo "   [CLEANUP] Lösche alle in der API-Antwort gelisteten Dateien im Pfad '$TARGET_PATH'..."
    CLEANUP_COUNT=0
    
    # Schleife über die Liste der Dateinamen aus der API-Antwort
    while read DELETE_FILENAME; do
        if [ -n "$DELETE_FILENAME" ]; then
            FILE_PATH="$TARGET_PATH/$DELETE_FILENAME"
            # Prüft, ob die Datei existiert und löscht sie dann
            if [ -f "$FILE_PATH" ]; then
                echo "     -> Lösche: $FILE_PATH"
                rm -f "$FILE_PATH"
                CLEANUP_COUNT=$((CLEANUP_COUNT + 1))
            fi
        fi
    done <<< "$FILES_TO_DELETE"

    echo "   [CLEANUP] $CLEANUP_COUNT alte Dateien gelöscht."
    
    
    # 4. Download (nur des neuesten Files)
    FINAL_PATH="$TARGET_PATH/$LATEST_FILENAME"
    echo "   [DOWNLOAD] Lade neueste Version herunter: $FINAL_PATH"
    
    # Der eigentliche Download: Folgt Redirects (-L) und schlägt bei Fehler fehl (--fail)
    curl --fail --show-error -L "$DOWNLOAD_URL" -o "$FINAL_PATH"
    
    if [ $? -eq 0 ]; then
        echo "   [SUCCESS] Mod $LATEST_FILENAME erfolgreich heruntergeladen."
    else
        echo "   [FATAL] Download-Fehler für $LATEST_FILENAME. Bitte prüfen Sie die URL."
    fi
    
    # 5. Aufräumen
    rm -f "$TEMP_RESPONSE"
    
    echo "--------------------------------------------------------"
    
done < "$TEMP_MODS_LIST"

# 6. Letztes Aufräumen der temporären Liste
rm -f "$TEMP_MODS_LIST"
echo "Alle Mod-Abfragen abgeschlossen."