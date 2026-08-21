#!/bin/bash

# Full photon production chain: GSD -> RECO -> nanoML -> parquet.
# useFineCalo=1, no pileup (PU=0).
#
# Usage: bash generate_photons_FineCalo.sh <output_directory> [n_events]
# Example: bash generate_photons_FineCalo.sh /path/to/output/folder 1000

if [ $# -eq 0 ]; then
    echo "Usage: $0 <output_directory> [n_events]"
    echo "Example: $0 /path/to/output/folder 1000"
    exit 1
fi

OUTPUT_DIR="$1"
N="${2:-1000}"

PARTICLE=22   # photon
PARTNAME="photons"
ENERGY=100.0

# python3 with uproot/awkward/pyarrow for the parquet conversion step (the
# CMSSW python from cmsenv does not have these). Override if the default
# python3 in your shell doesn't have them, e.g.:
#   export PARQUET_PYTHON=/path/to/conda/envs/<env>/bin/python3
PARQUET_PYTHON="${PARQUET_PYTHON:-python3}"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

if ! command -v cmsRun >/dev/null 2>&1; then
    echo "❌ cmsRun not found in PATH. Did you run 'cmsenv' in this CMSSW area first?"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(realpath "$OUTPUT_DIR")

echo "Simulating $N $PARTNAME events (useFineCalo=1, no pileup)..."
echo "Saving all output files to: $OUTPUT_DIR"
echo ""

echo "⏳ Running GSD step..."
cmsRun "$REPO_DIR/GSD_GUN.py" seed=1 maxEvents=$N useFineCalo=1 pileup=0 \
    particle=$PARTICLE energy=$ENERGY \
    outputFile="$OUTPUT_DIR/${PARTNAME}_GSD.root"
if [ $? -ne 0 ]; then
    echo "❌ Failed GSD step"
    exit 1
fi
echo ""

echo "⏳ Running RECO step..."
cmsRun "$REPO_DIR/RECO.py" \
    inputFiles="file:$OUTPUT_DIR/${PARTNAME}_GSD.root" \
    outputFile="$OUTPUT_DIR/${PARTNAME}_RECO.root" \
    outputFileDQM="$OUTPUT_DIR/${PARTNAME}_DQM.root"
if [ $? -ne 0 ]; then
    echo "❌ Failed RECO step"
    exit 1
fi
echo ""

echo "⏳ Running nanoML step..."
cmsRun "$REPO_DIR/nanoML_cfg.py" \
    inputFiles="file:$OUTPUT_DIR/${PARTNAME}_RECO.root" \
    outputFile="$OUTPUT_DIR/${PARTNAME}_nanoML.root"
if [ $? -ne 0 ]; then
    echo "❌ Failed nanoML step"
    exit 1
fi
echo ""

echo "⏳ Converting to parquet..."
"$PARQUET_PYTHON" "$REPO_DIR/root_to_parquet.py" \
    --nanoMLfiles "$OUTPUT_DIR/${PARTNAME}_nanoML.root" \
    --outputDir "$OUTPUT_DIR" \
    --outputFile "${PARTNAME}.parquet"
if [ $? -ne 0 ]; then
    echo "❌ Failed parquet conversion step"
    exit 1
fi
echo ""

echo "✅ All steps completed successfully!"
echo "All files have been saved to: $OUTPUT_DIR"
