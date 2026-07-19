#!/usr/bin/env bash
#
# generate_pipeline.sh – Full world generation pipeline
#
# Usage:
#   ./generate_pipeline.sh --name MyWorld --end-year 250 [--seed 42]
#
# Creates:
#   MyWorld/
#   ├── MyWorld.json
#   ├── MyWorld_font.png
#   ├── MyWorld_map.png
#   ├── MyWorld_loci.yaml
#   ├── MyWorld_memory.yaml
#   ├── MyWorld_questions.yaml
#   ├── MyWorld_probe_config.yaml
#   ├── Characters/
#   │   ├── CharacterName/
#   │   │   ├── CharacterName.json
#   │   │   ├── CharacterName_loci.yaml
#   │   │   ├── CharacterName_memory.yaml
#   │   │   └── CharacterName_questions.yaml
#   │   └── ...
#   ├── Beasts/
#   │   └── BeastName/
#   │       ├── BeastName.json
#   │       ├── BeastName_loci.yaml
#   │       ├── BeastName_memory.yaml
#   │       └── BeastName_questions.yaml
#   ├── Artifacts/
#   │   ├── ArtifactName/
#   │   │   ├── ArtifactName.json
#   │   │   ├── ArtifactName_loci.yaml
#   │   │   ├── ArtifactName_memory.yaml
#   │   │   └── ArtifactName_questions.yaml
#   │   └── ...
#   └── POIs/
#       ├── POIName/
#       │   ├── POIName.json
#       │   ├── POIName_map.png
#       │   ├── POIName_loci.yaml
#       │   ├── POIName_memory.yaml
#       │   └── POIName_questions.yaml

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse args ──────────────────────────────────────────────────────
NAME=""
END_YEAR=""
SEED=""
FONT_PATH=""
HAS_COLOR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)        NAME="$2";        shift 2 ;;
        --end-year)    END_YEAR="$2";    shift 2 ;;
        --seed)        SEED="$2";        shift 2 ;;
        --font)        FONT_PATH="$2";   shift 2 ;;
        --has-color)   HAS_COLOR="--has-color"; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ -z "$NAME" || -z "$END_YEAR" ]]; then
    echo "Usage: $0 --name WorldName --end-year 250 [--seed 42] [--font /path/to/font.png] [--has-color]"
    exit 1
fi

# ── Setup paths ──────────────────────────────────────────────────────
WORLD_DIR="$SCRIPT_DIR/$NAME"
mkdir -p "$WORLD_DIR"
mkdir -p "$WORLD_DIR/Characters"
mkdir -p "$WORLD_DIR/Beasts"
mkdir -p "$WORLD_DIR/Artifacts"
mkdir -p "$WORLD_DIR/POIs"

WORLD_JSON="$WORLD_DIR/${NAME}.json"

echo "=============================================="
echo "  Generating world: $NAME (end year $END_YEAR)"
echo "=============================================="

# ── Step 1: Generate world ───────────────────────────────────────────
SEED_ARGS=""
[[ -n "$SEED" ]] && SEED_ARGS="--seed $SEED"

python3 "$SCRIPT_DIR/world_gen.py" \
    $SEED_ARGS \
    --name "$NAME" \
    --end-year "$END_YEAR" \
    --output "$WORLD_JSON"

echo ""
echo "=== World generated: $WORLD_JSON ==="

# ── Step 1.5: Bitmap font ────────────────────────────────────────────
if [[ -n "$FONT_PATH" ]]; then
    # Use the provided font PNG; skip generation
    FONT_PNG="$FONT_PATH"
    echo ""
    echo "--- Using provided font: $FONT_PNG ---"
else
    # Generate a default font
    FONT_PNG="$WORLD_DIR/${NAME}_font.png"
    if [[ ! -f "$FONT_PNG" ]]; then
        echo ""
        echo "--- Generating bitmap font ---"
        python3 "$SCRIPT_DIR/bitmap_font.py" \
            --generate-font \
            --font-output "$FONT_PNG"
    else
        echo ""
        echo "--- Using cached font: $FONT_PNG ---"
    fi
fi

FONT_REF_PNG="$WORLD_DIR/${NAME}_font_reference.png"
echo "  Generating reference sheet → ${NAME}_font_reference.png"
python3 "$SCRIPT_DIR/bitmap_font.py" \
    --reference "$FONT_PNG" \
    --reference-output "$FONT_REF_PNG"

# ── Step 2: Pick random entities from world JSON ─────────────────────
# Pick N random indices from an array of length L
pick_random_ids() {
    local arr_len="$1"
    local count="$2"
    local rng_seed="${3:-42}"
    local ids=()
    local i=0
    while [[ $i -lt $count ]]; do
        local idx=$(( (rng_seed * (i + 1) * 3137 + 59) % arr_len ))
        ids+=("$idx")
        i=$((i + 1))
    done
    echo "${ids[@]}"
}

# Count entities
NUM_FIGURES=$(jq -r '.historical_figures | length' "$WORLD_JSON" 2>/dev/null || echo 0)
NUM_BEASTS=$(jq -r '.beasts | length' "$WORLD_JSON" 2>/dev/null || echo 0)
NUM_ARTIFACTS=$(jq -r '.artifacts | length' "$WORLD_JSON" 2>/dev/null || echo 0)
NUM_SITES=$(jq -r '.sites | length' "$WORLD_JSON" 2>/dev/null || echo 0)

echo "Entities available: $NUM_FIGURES figures, $NUM_BEASTS beasts, $NUM_ARTIFACTS artifacts, $NUM_SITES sites"

# We need: 3 characters, 1 beast, 2 artifacts, 4 POIs (sites)
# Use deterministic selection from seed (or 42)
SELECT_SEED="${SEED:-42}"

CHARGE_IDS=($(pick_random_ids "$NUM_FIGURES" 3 "$SELECT_SEED"))
BEAST_IDS=($(pick_random_ids "$NUM_BEASTS" 1 "$((SELECT_SEED + 1))"))
ARTIFACT_IDS=($(pick_random_ids "$NUM_ARTIFACTS" 2 "$((SELECT_SEED + 2))"))
POI_IDS=($(pick_random_ids "$NUM_SITES" 4 "$((SELECT_SEED + 3))"))

echo ""
echo "Selected character indices: ${CHARGE_IDS[*]}"
echo "Selected beast indices:     ${BEAST_IDS[*]}"
echo "Selected artifact indices:  ${ARTIFACT_IDS[*]}"
echo "Selected POI indices:       ${POI_IDS[*]}"

# ── Helper: generate loci + memory YAML from a memory JSON file ──────
generate_entity_yamls() {
    local entity_type="$1"     # "character", "beast", "artifact", "poi"
    local memory_json="$2"
    local output_dir="$3"
    local entity_name="$4"     # safe filesystem name for the entity

    # loci YAML — name it with the entity name
    python3 "$SCRIPT_DIR/memory_to_loci.py" \
        --"$entity_type" "$memory_json" \
        --output "$output_dir/${entity_name}_loci.yaml" 2>/dev/null

    # memory YAML — name it with the entity name
    python3 "$SCRIPT_DIR/memory_to_seren.py" \
        --"$entity_type" "$memory_json" \
        --output "$output_dir/${entity_name}_memory.yaml" 2>/dev/null
}

# ── Helper: generate questions YAML from entity data ──────────────────
generate_entity_questions() {
    local entity_type="$1"     # "character", "beast", "artifact", "poi"
    local memory_json="$2"
    local output_dir="$3"
    local entity_name="$4"     # safe filesystem name for the entity

    # NOTE: stderr is NOT redirected. question_gen.py reports its memory skip
    # counts and skipped refs on stderr -- "N anchored, M skipped" is the only
    # place corpus duplication becomes visible. Sending it to /dev/null meant
    # every entity generated silently while only the world-level call (which
    # never had the redirect) showed its diagnostics. A filter reporting what's
    # clean answers "what didn't match me", not "what is real".
    python3 "$SCRIPT_DIR/question_gen.py" \
        --entity-type "$entity_type" \
        --entity-json "$memory_json" \
        --entity-loci "$output_dir/${entity_name}_loci.yaml" \
        --entity-memory "$output_dir/${entity_name}_memory.yaml" \
        --world "$WORLD_JSON" \
        --output "$output_dir/${entity_name}_questions.yaml" \
        --seed "$((SELECT_SEED + 500))" \
        --target-count 10
}

# ── Step 3: Process world-level ─────────────────────────────────────
echo ""
echo "--- World-level exports ---"
python3 "$SCRIPT_DIR/memory_to_loci.py" \
    --world "$WORLD_JSON" \
    --output "$WORLD_DIR/${NAME}_loci.yaml"

python3 "$SCRIPT_DIR/memory_to_seren.py" \
    --world "$WORLD_JSON" \
    --output "$WORLD_DIR/${NAME}_memory.yaml"

# ── Step 3.3: Generate world-level questions ──────────────────────────
echo ""
echo "--- World-level questions ---"
python3 "$SCRIPT_DIR/question_gen.py" \
    --entity-type world \
    --entity-loci "$WORLD_DIR/${NAME}_loci.yaml" \
    --entity-memory "$WORLD_DIR/${NAME}_memory.yaml" \
    --world "$WORLD_JSON" \
    --output "$WORLD_DIR/${NAME}_questions.yaml" \
    --seed "$SELECT_SEED" \
    --target-count 10

# ── Step 3.5: Render world map ───────────────────────────────────────
WORLD_MAP_PNG="$WORLD_DIR/${NAME}_map.png"
echo ""
echo "--- World map ---"
echo "  Rendering → ${NAME}_map.png"
python3 "$SCRIPT_DIR/map_renderer.py" \
    --world "$WORLD_JSON" \
    --font "$FONT_PNG" \
    --output "$WORLD_MAP_PNG" \
    $HAS_COLOR

# ── Step 4: Process characters ───────────────────────────────────────
echo ""
echo "--- Characters ---"
for idx in "${CHARGE_IDS[@]}"; do
    FIGURE_ID=$(jq -r ".historical_figures[$idx].id" "$WORLD_JSON")
    FIGURE_NAME=$(jq -r ".historical_figures[$idx].name" "$WORLD_JSON")
    # Sanitize name for filesystem
    SAFE_NAME=$(echo "$FIGURE_NAME" | tr ' /' '__')
    CHAR_DIR="$WORLD_DIR/Characters/$SAFE_NAME"
    mkdir -p "$CHAR_DIR"

    CHAR_JSON="$CHAR_DIR/${SAFE_NAME}.json"
    echo "  $SAFE_NAME (id=$FIGURE_ID) ..."

    python3 "$SCRIPT_DIR/character_memory_gen.py" \
        --world "$WORLD_JSON" \
        --char-id "$FIGURE_ID" \
        --seed "$((SELECT_SEED + idx * 7))" \
        --output "$CHAR_JSON"

    generate_entity_yamls "character" "$CHAR_JSON" "$CHAR_DIR" "$SAFE_NAME"
    generate_entity_questions "character" "$CHAR_JSON" "$CHAR_DIR" "$SAFE_NAME"
    echo "    -> ${SAFE_NAME}_loci.yaml + ${SAFE_NAME}_memory.yaml + ${SAFE_NAME}_questions.yaml"
done

# ── Step 5: Process beast ────────────────────────────────────────────
echo ""
echo "--- Beasts ---"
for idx in "${BEAST_IDS[@]}"; do
    BEAST_ID=$(jq -r ".beasts[$idx].id" "$WORLD_JSON")
    BEAST_NAME=$(jq -r ".beasts[$idx].name" "$WORLD_JSON")
    SAFE_NAME=$(echo "$BEAST_NAME" | tr ' /' '__')
    BEAST_DIR="$WORLD_DIR/Beasts/$SAFE_NAME"
    mkdir -p "$BEAST_DIR"

    BEAST_JSON="$BEAST_DIR/${SAFE_NAME}.json"
    echo "  $SAFE_NAME (id=$BEAST_ID) ..."

    python3 "$SCRIPT_DIR/ooi_memory_gen.py" \
        --world "$WORLD_JSON" \
        --ooi-type "beast" \
        --ooi-id "$BEAST_ID" \
        --seed "$((SELECT_SEED + 100))" \
        --output "$BEAST_JSON"

    generate_entity_yamls "ooi" "$BEAST_JSON" "$BEAST_DIR" "$SAFE_NAME"
    generate_entity_questions "beast" "$BEAST_JSON" "$BEAST_DIR" "$SAFE_NAME"
    echo "    -> ${SAFE_NAME}_loci.yaml + ${SAFE_NAME}_memory.yaml + ${SAFE_NAME}_questions.yaml"
done

# ── Step 6: Process artifacts ────────────────────────────────────────
echo ""
echo "--- Artifacts ---"
for idx in "${ARTIFACT_IDS[@]}"; do
    ART_ID=$(jq -r ".artifacts[$idx].id" "$WORLD_JSON")
    ART_NAME=$(jq -r ".artifacts[$idx].name" "$WORLD_JSON")
    SAFE_NAME=$(echo "$ART_NAME" | tr ' /' '__')
    ART_DIR="$WORLD_DIR/Artifacts/$SAFE_NAME"
    mkdir -p "$ART_DIR"

    ART_JSON="$ART_DIR/${SAFE_NAME}.json"
    echo "  $SAFE_NAME (id=$ART_ID) ..."

    python3 "$SCRIPT_DIR/ooi_memory_gen.py" \
        --world "$WORLD_JSON" \
        --ooi-type "artifact" \
        --ooi-id "$ART_ID" \
        --seed "$((SELECT_SEED + 200 + idx * 3))" \
        --output "$ART_JSON"

    generate_entity_yamls "ooi" "$ART_JSON" "$ART_DIR" "$SAFE_NAME"
    generate_entity_questions "artifact" "$ART_JSON" "$ART_DIR" "$SAFE_NAME"
    echo "    -> ${SAFE_NAME}_loci.yaml + ${SAFE_NAME}_memory.yaml + ${SAFE_NAME}_questions.yaml"
done

# ── Step 7: Process POIs (sites) ─────────────────────────────────────
echo ""
echo "--- POIs ---"
for idx in "${POI_IDS[@]}"; do
    SITE_ID=$(jq -r ".sites[$idx].id" "$WORLD_JSON")
    SITE_NAME=$(jq -r ".sites[$idx].name" "$WORLD_JSON")
    SAFE_NAME=$(echo "$SITE_NAME" | tr ' /' '__')
    POI_DIR="$WORLD_DIR/POIs/$SAFE_NAME"
    mkdir -p "$POI_DIR"

    POI_JSON="$POI_DIR/${SAFE_NAME}.json"
    echo "  $SAFE_NAME (id=$SITE_ID) ..."

    python3 "$SCRIPT_DIR/poi_memory_gen.py" \
        --world "$WORLD_JSON" \
        --poi-type "site" \
        --poi-id "$SITE_ID" \
        --seed "$((SELECT_SEED + 300 + idx * 5))" \
        --output "$POI_JSON"

    generate_entity_yamls "poi" "$POI_JSON" "$POI_DIR" "$SAFE_NAME"
    generate_entity_questions "poi" "$POI_JSON" "$POI_DIR" "$SAFE_NAME"

    # ── Step 7.5: Render POI local map ────────────────────────────────
    POI_MAP_PNG="$POI_DIR/${SAFE_NAME}_map.png"
    echo "  Rendering local map → ${SAFE_NAME}_map.png"
    python3 "$SCRIPT_DIR/poi_map_renderer.py" \
        --world "$WORLD_JSON" \
        --poi-id "$SITE_ID" \
        --font "$FONT_PNG" \
        --output "$POI_MAP_PNG" \
        $HAS_COLOR

    echo "    -> ${SAFE_NAME}_loci.yaml + ${SAFE_NAME}_memory.yaml + ${SAFE_NAME}_questions.yaml + ${SAFE_NAME}_map.png"
done

# ── Step 7.9: Cross-corpus question sets ─────────────────────────────
#
# MUST run before Step 8: probe_config_gen writes a Questions: entry pointing at
# each of these files, and a corpus whose question file does not exist seeds
# fine and then evaluates zero questions -- a silent empty column, not an error.
#
# Membership follows which generator produced the entity, so these three sets
# stay in sync with the pipeline automatically:
#   characters = character_memory_gen + ooi_memory_gen (chars, beasts, artifacts)
#   geography  = poi_memory_gen + the world's own loci/memory
#   all        = the union
echo ""
echo "--- Cross-corpus questions ---"
for SCOPE in characters geography all; do
    case "$SCOPE" in
        characters) LABEL="Characters" ;;
        geography)  LABEL="Geography"  ;;
        all)        LABEL="All"        ;;
    esac
    echo "  ${LABEL}_questions.yaml"
    python3 "$SCRIPT_DIR/cross_question_gen.py" \
        --root "$WORLD_DIR" \
        --world-name "$NAME" \
        --scope "$SCOPE" \
        --output "$WORLD_DIR/${LABEL}_questions.yaml" \
        --seed "$((SELECT_SEED + 900))" \
        --per-member 3
done

# ── Step 8: Generate SerenProbe probe config ──────────────────────────
echo ""
echo "--- Probe config ---"
echo "  Generating ${NAME}_probe_config.yaml"
python3 "$SCRIPT_DIR/probe_config_gen.py" \
    --world-dir "$WORLD_DIR" \
    --city-name "$NAME" \
    --output "$WORLD_DIR/${NAME}_probe_config.yaml" \
    --starting-port 7620

echo "    -> ${NAME}_probe_config.yaml"

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  Pipeline complete for $NAME"
echo "=============================================="
echo ""
echo "Directory structure:"
find "$WORLD_DIR" -type f | sort | while read -r f; do
    echo "  $f"
done
echo ""
echo "Done."
