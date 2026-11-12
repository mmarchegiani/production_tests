#!/bin/bash
# run_parquet.sh — one node of the parquet-conversion DAG (see submit_parquet.sh).
#
# All arguments are key=value pairs, in any order:
#
#   stage=convert|metadata
#   infiles=A,B,...  convert: nanoML ROOT files, comma-separated, all merged into
#                    one parquet file. Files on CERN EOS (/eos/user/...) are
#                    fetched into the job sandbox with xrdcp (kerberos is
#                    forwarded by CERN HTCondor); any other path is read in place
#   infile=PATH      convert: a single input (same as infiles= with one entry)
#   outdir=URL       folder for the parquet files: root://host//path (streamed;
#                    needs the VOMS proxy that HTCondor puts in X509_USER_PROXY,
#                    except for CERN EOS hosts, which take the forwarded kerberos
#                    ticket) or a local directory
#   outfile=NAME     convert: parquet file name
#   step=N           convert: --step_size (default 500 = one row group per 500 events)
#   maxevents=N      convert: --max_events per input file (tests only)
#   force=0|1        convert: 1 overwrites an existing output; 0 (default) skips
#                    the conversion and exits 0 when the output already exists,
#                    so a resubmitted DAG only redoes the missing files
#   prodtests=PATH   directory holding root_to_parquet-split.py and
#                    parquet_folder_metadata.py (default: this script's parent)
#   lcg=PATH         setup.sh of the LCG view providing python3 with pyarrow,
#                    uproot, awkward and the XRootD bindings (default LCG_109 el9)
#
# stage=convert  : <infiles> -> <outdir>/<outfile>  (root_to_parquet-split.py)
# stage=metadata : writes <outdir>/_metadata and _common_metadata so that the
#                  folder reads as one dataset (parquet_folder_metadata.py)
set -eu -o pipefail
shopt -s extglob

STAGE=""; INFILES=(); OUTDIR=""; OUTFILE=""; STEP=500; MAXEVENTS=""; FORCE=0
PRODTESTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LCG=/cvmfs/sft.cern.ch/lcg/views/LCG_109/x86_64-el9-gcc13-opt/setup.sh

for kv in "$@"; do
    key="${kv%%=*}"; val="${kv#*=}"
    case "$key" in
        stage)     STAGE="$val" ;;
        infile)    INFILES+=("$val") ;;
        infiles)   IFS=, read -r -a _list <<< "$val"; INFILES+=("${_list[@]}") ;;
        outdir)    OUTDIR="$val" ;;
        outfile)   OUTFILE="$val" ;;
        step)      STEP="$val" ;;
        maxevents) MAXEVENTS="$val" ;;
        force)     FORCE="$val" ;;
        prodtests) PRODTESTS="$val" ;;
        lcg)       LCG="$val" ;;
        *) echo "ERROR: unknown argument '$kv'" >&2; exit 1 ;;
    esac
done
[ -n "$STAGE" ] && [ -n "$OUTDIR" ] || { echo "ERROR: stage= and outdir= are required" >&2; exit 1; }

echo "=== run_parquet.sh stage=$STAGE on $(hostname) at $(date)"
echo "    outdir=$OUTDIR outfile=${OUTFILE:-} step=$STEP force=$FORCE"
echo "    inputs (${#INFILES[@]}): ${INFILES[*]:-}"

# --- environment ------------------------------------------------------------
# The LCG setup script clobbers unrelated shell variables (e.g. BASE), uses
# unset ones and has non-zero intermediate statuses, so it is sourced only
# after the arguments above have been parsed and with -eu switched off.
[ -f "$LCG" ] || { echo "ERROR: LCG setup script not found: $LCG" >&2; exit 1; }
set +eu
source "$LCG"
set -eu
echo "    python3: $(command -v python3) ($(python3 --version 2>&1))"

case "$OUTDIR" in
    root://*)
        case "$OUTDIR" in
            root://eoscms.cern.ch*|root://eosuser.cern.ch*|root://eoshome*.cern.ch*|root://eosproject*.cern.ch*)
                # CERN EOS: the kerberos ticket forwarded by HTCondor (or the
                # interactive one) authenticates the XRootD client, no proxy.
                klist -s 2>/dev/null || { echo "ERROR: no valid kerberos ticket for $OUTDIR" >&2; exit 1; }
                echo "    auth: kerberos ($(klist 2>/dev/null | awk '/Default principal/ {print $3}'))"
                ;;
            *)
                # VOMS proxy for the remote storage: HTCondor sets X509_USER_PROXY
                # from the x509userproxy submit attribute; fall back to the usual
                # location for interactive runs.
                : "${X509_USER_PROXY:=/tmp/x509up_u$(id -u)}"
                export X509_USER_PROXY
                [ -f "$X509_USER_PROXY" ] || { echo "ERROR: no VOMS proxy at $X509_USER_PROXY" >&2; exit 1; }
                echo "    proxy: $X509_USER_PROXY"
                ;;
        esac
        REMOTE=1
        XRD_HOST="${OUTDIR#root://}"; XRD_HOST="root://${XRD_HOST%%/*}"
        XRD_DIR="/${OUTDIR#root://*/}"; XRD_DIR="/${XRD_DIR##+(/)}"   # root://h//p -> /p
        ;;
    *)  REMOTE=0; mkdir -p "$OUTDIR" ;;
esac

# eos_url PATH -> xrootd URL for CERN EOS paths, else PATH unchanged
eos_url() {
    case "$1" in
        /eos/user/*|/eos/home-*) echo "root://eosuser.cern.ch/$1" ;;
        /eos/cms/*)              echo "root://eoscms.cern.ch/$1" ;;
        *)                       echo "$1" ;;
    esac
}

# xrd_retry <xrdcp args...> : up to 3 attempts
xrd_retry() {
    local n
    for n in 1 2 3; do
        xrdcp -f "$@" && return 0
        echo "xrdcp attempt $n failed, retrying in 30 s" >&2
        sleep 30
    done
    return 1
}

output_exists() {
    if [ "$REMOTE" -eq 1 ]; then
        xrdfs "$XRD_HOST" stat "$XRD_DIR/$OUTFILE" >/dev/null 2>&1
    else
        [ -f "$OUTDIR/$OUTFILE" ]
    fi
}

case "$STAGE" in
    convert)
        [ "${#INFILES[@]}" -gt 0 ] && [ -n "$OUTFILE" ] || { echo "ERROR: convert needs infiles= (or infile=) and outfile=" >&2; exit 1; }
        if [ "$FORCE" != 1 ] && output_exists; then
            echo "output $OUTDIR/$OUTFILE already exists, nothing to do (force=1 to redo)"
            exit 0
        fi
        # Stage the inputs into the sandbox: a local read is faster and more
        # robust than uproot over xrootd / EOS fuse on the worker node.
        LOCAL_INS=(); FETCHED=()
        for f in "${INFILES[@]}"; do
            SRC="$(eos_url "$f")"
            case "$SRC" in
                root://*)
                    local_f="$PWD/$(basename "$f")"
                    echo "    fetching $SRC"
                    xrd_retry "$SRC" "$local_f"
                    FETCHED+=("$local_f")
                    ;;
                *)  local_f="$f" ;;
            esac
            [ -f "$local_f" ] || { echo "ERROR: input not found: $local_f" >&2; exit 1; }
            LOCAL_INS+=("$local_f")
        done

        EXTRA=()
        [ -n "$MAXEVENTS" ] && EXTRA+=(--max_events "$MAXEVENTS")
        [ "$FORCE" = 1 ] && EXTRA+=(--overwrite)
        /usr/bin/time -f "    convert: %e s wall, %M KB max RSS" \
            python3 -u "$PRODTESTS/root_to_parquet-split.py" \
                --nanoMLfiles "${LOCAL_INS[@]}" \
                --outputDir "$OUTDIR" --outputFile "$OUTFILE" \
                --step_size "$STEP" "${EXTRA[@]}"
        [ "${#FETCHED[@]}" -eq 0 ] || rm -f "${FETCHED[@]}"
        ;;
    metadata)
        python3 -u "$PRODTESTS/parquet_folder_metadata.py" "$OUTDIR"
        ;;
    *) echo "ERROR: unknown stage '$STAGE'" >&2; exit 1 ;;
esac
echo "=== done at $(date)"
