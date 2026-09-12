#!/bin/bash
#
# Shared driver for the one-shot production chains in scripts/.
#
# This file is *not* meant to be executed directly. Each of the four wrappers
#
#   generate_photons_FineCalo.sh    generate_photons_noFineCalo.sh
#   generate_taus_FineCalo.sh       generate_taus_noFineCalo.sh
#
# sets PARTICLE / PARTNAME / USE_FINE_CALO, sources this file and calls
#
#   run_chain "$@"
#
# which runs   [MINBIAS] -> GSD -> RECO -> nanoML -> parquet
# as a sequence of local `cmsRun` invocations (single machine, no batch system).
# For large campaigns use condor/ (LPC HTCondor+DAGMan) or
# condor/submit_pipeline.slurm (falcon) instead.

# Propagate failures through the `| tee` pipelines used for step logging.
set -o pipefail

chain_usage() {
    local pu_note="  --pileup N             average pileup (default: 0 = no PU)
  --pu FILE              existing minbias GEN-SIM file to mix (implies --pileup if unset);
                         when --pileup > 0 and --pu is not given, MINBIAS_GENSIM.py is run first
  --nminbias N           events for the auto-generated minbias library (default: n_events)"
    if [ "$USE_FINE_CALO" -eq 1 ]; then
        pu_note="  --pileup / --pu / --nminbias
                         NOT supported with useFineCalo=1 (GSDfineCalo_fragment has no
                         PU mixing) -- use the noFineCalo script for pileup samples"
    fi
    cat <<EOF
Usage: $CHAIN_SCRIPT_NAME <output_directory> [n_events] [options]

Runs the full ${PARTNAME} chain (useFineCalo=${USE_FINE_CALO}):
[MINBIAS] -> GSD -> RECO -> nanoML -> parquet.

Positional:
  <output_directory>     where all ROOT/parquet/log files are written (created if missing)
  [n_events]             number of signal events to simulate (default: 1000)

Options:
  --seed N               random seed for the GSD step (default: 1)
  --minE X               gun minimum energy in GeV (default: 20.0)
  --maxE X               gun maximum energy in GeV (default: 200.0)
  --nthreads N           threads per cmsRun step (default: 1)
${pu_note}
  --run-pftruth          enable the PFTruth sequence in nanoML (broken with pileup)
  --split                use root_to_parquet-split.py (one row per HGCAL endcap)
  --no-parquet           stop after the nanoML step
  --resume               skip any step whose output file already exists
  --dry-run              print the commands that would run, execute nothing
  -h, --help             show this help

Environment:
  PARQUET_PYTHON         python3 with uproot/awkward/pyarrow for the parquet step
                         (the CMSSW python from cmsenv does not have these).
                         Default: python3
                         e.g. export PARQUET_PYTHON=/path/to/conda/envs/<env>/bin/python3

Example:
  $CHAIN_SCRIPT_NAME /eos/user/\$USER/${PARTNAME}_sample 2000 --seed 7 --nthreads 4
EOF
}

chain_die() {
    echo "❌ $*" >&2
    exit 1
}

# run_step <description> <logfile> <command...>
# Honours --dry-run, tees combined output to <logfile>, aborts the chain on failure.
chain_run_step() {
    local desc="$1"; shift
    local logfile="$1"; shift

    echo "⏳ ${desc}..."
    printf '   '; printf '%q ' "$@"; printf '\n'

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "   (dry-run: not executed)"
        echo ""
        return 0
    fi

    "$@" 2>&1 | tee "$logfile"
    local rc=$?
    if [ $rc -ne 0 ]; then
        chain_die "Failed: $desc (exit $rc) — see $logfile"
    fi
    echo ""
}

# chain_skip <step name> <output file>  -> 0 if the step can be skipped (--resume)
chain_skip() {
    if [ "$RESUME" -eq 1 ] && [ -s "$2" ]; then
        echo "⏭  Skipping $1 — $2 already exists (--resume)"
        echo ""
        return 0
    fi
    return 1
}

run_chain() {
    CHAIN_SCRIPT_NAME="$(basename "${BASH_SOURCE[1]}")"

    local SCRIPT_DIR REPO_DIR
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    REPO_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

    # ---- defaults -------------------------------------------------------
    local OUTPUT_DIR="" NEVENTS=1000
    local SEED=1 MIN_ENERGY=20.0 MAX_ENERGY=200.0 NTHREADS=1
    local PILEUP=0 PU_FILE="" NMINBIAS=""
    local RUN_PFTRUTH=0 DO_PARQUET=1 SPLIT=0
    RESUME=0
    DRY_RUN=0
    local PARQUET_PY="${PARQUET_PYTHON:-python3}"

    # ---- argument parsing ----------------------------------------------
    local positional=()
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)     chain_usage; exit 0 ;;
            --seed)        SEED="$2";        shift 2 ;;
            --minE|--mine) MIN_ENERGY="$2";  shift 2 ;;
            --maxE|--maxe) MAX_ENERGY="$2";  shift 2 ;;
            --nthreads)    NTHREADS="$2";    shift 2 ;;
            --pileup)      PILEUP="$2";      shift 2 ;;
            --pu)          PU_FILE="$2";     shift 2 ;;
            --nminbias)    NMINBIAS="$2";    shift 2 ;;
            --run-pftruth) RUN_PFTRUTH=1;    shift ;;
            --split)       SPLIT=1;          shift ;;
            --no-parquet)  DO_PARQUET=0;     shift ;;
            --resume)      RESUME=1;         shift ;;
            --dry-run)     DRY_RUN=1;        shift ;;
            -*)            chain_usage >&2; chain_die "Unknown option: $1" ;;
            *)             positional+=("$1"); shift ;;
        esac
    done

    if [ ${#positional[@]} -eq 0 ]; then
        chain_usage >&2
        exit 1
    fi
    OUTPUT_DIR="${positional[0]}"
    [ ${#positional[@]} -ge 2 ] && NEVENTS="${positional[1]}"
    [ ${#positional[@]} -gt 2 ] && chain_die "Too many positional arguments: ${positional[*]}"

    # A --pu file without an explicit --pileup still means "mix pileup".
    if [ -n "$PU_FILE" ] && [ "$PILEUP" -eq 0 ]; then
        PILEUP=30
        echo "ℹ  --pu given without --pileup: defaulting to pileup=$PILEUP"
    fi
    [ -z "$NMINBIAS" ] && NMINBIAS="$NEVENTS"

    # ---- sanity checks --------------------------------------------------
    if [ "$DRY_RUN" -eq 0 ] && ! command -v cmsRun >/dev/null 2>&1; then
        chain_die "cmsRun not found in PATH. Did you run 'cmsenv' in this CMSSW area first?"
    fi

    # GSDfineCalo_fragment has no pileup-mixing wiring (see GSD_GUN.py).
    if [ "$USE_FINE_CALO" -eq 1 ] && [ "$PILEUP" -gt 0 ]; then
        chain_die "useFineCalo=1 does not support pileup (GSDfineCalo_fragment has no PU mixing). Use the noFineCalo variant of this script for PU samples."
    fi

    if [ "$PILEUP" -gt 0 ] && [ "$RUN_PFTRUTH" -eq 1 ]; then
        chain_die "--run-pftruth is incompatible with pileup (the PFTruth sequence is broken with PU)."
    fi

    mkdir -p "$OUTPUT_DIR" || chain_die "Cannot create output directory: $OUTPUT_DIR"
    OUTPUT_DIR=$(realpath "$OUTPUT_DIR")
    local LOG_DIR="$OUTPUT_DIR/logs"
    mkdir -p "$LOG_DIR"

    # ---- file naming ----------------------------------------------------
    # The calo tag keeps FineCalo and non-FineCalo samples from clobbering
    # each other when both are produced into the same directory.
    local CALO_TAG="stdCalo"
    [ "$USE_FINE_CALO" -eq 1 ] && CALO_TAG="fineCalo"
    local TAG="${PARTNAME}_${CALO_TAG}"
    [ "$PILEUP" -gt 0 ] && TAG="${TAG}_PU${PILEUP}"

    local MINBIAS_FILE="$OUTPUT_DIR/${TAG}_MINBIAS.root"
    local GSD_FILE="$OUTPUT_DIR/${TAG}_GSD.root"
    local RECO_FILE="$OUTPUT_DIR/${TAG}_RECO.root"
    local DQM_FILE="$OUTPUT_DIR/${TAG}_DQM.root"
    local NANO_FILE="$OUTPUT_DIR/${TAG}_nanoML.root"
    local PARQUET_NAME="${TAG}.parquet"
    [ "$SPLIT" -eq 1 ] && PARQUET_NAME="${TAG}_split.parquet"
    local PARQUET_SCRIPT="root_to_parquet.py"
    [ "$SPLIT" -eq 1 ] && PARQUET_SCRIPT="root_to_parquet-split.py"

    # ---- banner ---------------------------------------------------------
    echo "=============================================================="
    echo " ${PARTNAME} production chain (pdgId=${PARTICLE}, useFineCalo=${USE_FINE_CALO})"
    echo "=============================================================="
    echo "  events        : $NEVENTS"
    echo "  seed          : $SEED"
    echo "  energy range  : ${MIN_ENERGY} - ${MAX_ENERGY} GeV"
    echo "  threads       : $NTHREADS"
    if [ "$PILEUP" -gt 0 ]; then
        if [ -n "$PU_FILE" ]; then
            echo "  pileup        : $PILEUP (mixing $PU_FILE)"
        else
            echo "  pileup        : $PILEUP ($NMINBIAS minbias events generated first)"
        fi
    else
        echo "  pileup        : 0 (none)"
    fi
    echo "  runPFTruth    : $RUN_PFTRUTH"
    if [ "$DO_PARQUET" -eq 1 ]; then
        echo "  parquet       : $PARQUET_SCRIPT -> $PARQUET_NAME"
    else
        echo "  parquet       : disabled (--no-parquet)"
    fi
    echo "  output dir    : $OUTPUT_DIR"
    echo "  logs          : $LOG_DIR"
    [ "$DRY_RUN" -eq 1 ] && echo "  MODE          : DRY RUN (nothing is executed)"
    echo ""

    # ---- step 1: MINBIAS (only when pileup is requested) -----------------
    if [ "$PILEUP" -gt 0 ] && [ -z "$PU_FILE" ]; then
        # Offset the minbias seed from the signal seed, as condor/run_stage.sh does.
        local MINBIAS_SEED=$((SEED + 500000))
        if ! chain_skip "MINBIAS step" "$MINBIAS_FILE"; then
            chain_run_step "Running MINBIAS step (${NMINBIAS} events, seed=${MINBIAS_SEED})" \
                "$LOG_DIR/${TAG}_minbias.log" \
                cmsRun "$REPO_DIR/MINBIAS_GENSIM.py" \
                    seed="$MINBIAS_SEED" \
                    maxEvents="$NMINBIAS" \
                    nThreads="$NTHREADS" \
                    outputFile="$MINBIAS_FILE"
        fi
        PU_FILE="$MINBIAS_FILE"
    fi

    # ---- step 2: GSD ------------------------------------------------------
    local gsd_args=(
        seed="$SEED"
        maxEvents="$NEVENTS"
        nThreads="$NTHREADS"
        useFineCalo="$USE_FINE_CALO"
        pileup="$PILEUP"
        particle="$PARTICLE"
        minE="$MIN_ENERGY"
        maxE="$MAX_ENERGY"
        outputFile="$GSD_FILE"
    )
    [ "$PILEUP" -gt 0 ] && gsd_args+=(pu="$PU_FILE")

    if ! chain_skip "GSD step" "$GSD_FILE"; then
        chain_run_step "Running GSD step" "$LOG_DIR/${TAG}_gsd.log" \
            cmsRun "$REPO_DIR/GSD_GUN.py" "${gsd_args[@]}"
    fi

    # ---- step 3: RECO -----------------------------------------------------
    if ! chain_skip "RECO step" "$RECO_FILE"; then
        chain_run_step "Running RECO step" "$LOG_DIR/${TAG}_reco.log" \
            cmsRun "$REPO_DIR/RECO.py" \
                nThreads="$NTHREADS" \
                inputFiles="file:$GSD_FILE" \
                outputFile="$RECO_FILE" \
                outputFileDQM="$DQM_FILE"
    fi

    # ---- step 4: nanoML ---------------------------------------------------
    if ! chain_skip "nanoML step" "$NANO_FILE"; then
        chain_run_step "Running nanoML step" "$LOG_DIR/${TAG}_nanoml.log" \
            cmsRun "$REPO_DIR/nanoML_cfg.py" \
                nThreads="$NTHREADS" \
                runPFTruth="$RUN_PFTRUTH" \
                inputFiles="file:$RECO_FILE" \
                outputFile="$NANO_FILE"
    fi

    # ---- step 5: parquet --------------------------------------------------
    if [ "$DO_PARQUET" -eq 1 ]; then
        if ! chain_skip "parquet step" "$OUTPUT_DIR/$PARQUET_NAME"; then
            chain_run_step "Converting to parquet ($PARQUET_SCRIPT)" \
                "$LOG_DIR/${TAG}_parquet.log" \
                "$PARQUET_PY" "$REPO_DIR/$PARQUET_SCRIPT" \
                    --nanoMLfiles "$NANO_FILE" \
                    --outputDir "$OUTPUT_DIR" \
                    --outputFile "$PARQUET_NAME"
        fi
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "✅ Dry run complete — no commands were executed."
        return 0
    fi

    echo "✅ All steps completed successfully!"
    echo "All files have been saved to: $OUTPUT_DIR"
}
