#!/bin/bash
# submit_parquet.sh — convert the nanoML ROOT files of one or more campaigns to
# endcap-split parquet with HTCondor jobs (CERN lxplus), streamed to an XRootD
# folder (e.g. the CMU T3), and write _metadata/_common_metadata in every output
# folder so that it reads as one dataset (ak.from_parquet(folder)).
#
# Two layouts:
#   default   one job and one parquet file per ROOT file of --indir, all in
#             --outdir (big campaigns: the files convert in parallel and the
#             folder is read as one dataset through _metadata)
#   --merge   one job per input directory, all its ROOT files merged into ONE
#             parquet file in --outdir/<dataset>/, <dataset> being the name of
#             the input directory (of its parent when the directory is called
#             nanoML): the lxplus naming scheme is kept on the remote side
#
# Builds a DAGMan DAG in --workdir (conversion nodes -> metadata node per
# output folder) and submits it. Every node runs condor/run_parquet.sh.
#
# Usage: submit_parquet.sh --indir DIR [--indir DIR ...] --outdir URL --workdir DIR
#          [--merge] [--pattern '*_nanoML.root'] [--step-size 500] [--ncpus 2] [--memory MB]
#          [--disk KB] [--flavour longlunch] [--retry 2] [--force] [--no-metadata]
#          [--proxy FILE] [--lcg SETUP.sh] [--tag NAME] [--schedd NAME] [--dry-run] [DIR ...]
#
#   --indir       directory with nanoML ROOT files (CERN EOS, read with xrdcp);
#                 with --merge it is repeatable and extra positional arguments
#                 (e.g. an expanded glob) count as input directories too
#   --outdir      destination (root://host//path or a local/shared path): the
#                 output folder itself (default layout) or the parent of the
#                 per-dataset folders (--merge). Output names: <input basename
#                 without _nanoML.root>_split.parquet, or with --merge the files'
#                 common prefix before _rep<N> (photons_stdCalo_E10_split.parquet)
#   --workdir     DAG, submit files and logs (AFS, not EOS). The CERN schedds
#                 refuse a submit file whose executable, log or input lives on
#                 /eos (SUBMIT_REQUIREMENT_NoEos), so run_parquet.sh is copied
#                 next to the DAG and the submit files point at that copy; the
#                 scripts' own directory (prodtests=) may be on EOS, it only
#                 appears in the job arguments.
#   --force       overwrite existing outputs (default: jobs whose output already
#                 exists exit immediately, so resubmitting only fills the gaps)
#   --tag         name used for the DAG/submit files (default: basename of --outdir)
#   --proxy       VOMS proxy to ship with the jobs (default $X509_USER_PROXY or
#                 /tmp/x509up_u<uid>). It is copied into <workdir>/proxy/, whose
#                 AFS ACL is restricted to you, because DAGMan submits the node
#                 jobs from the schedd, where /tmp of this machine is not visible.
#                 Not needed for a CERN EOS destination (root://eoscms.cern.ch,
#                 root://eosuser.cern.ch, ...): the jobs use the kerberos ticket
#                 that CERN HTCondor forwards, like the production DAGs.
#
# Must be run from a shell where cmsenv has NOT been sourced (it leaks
# PYTHONHOME/PYTHONPATH/LD_LIBRARY_PATH, which breaks condor_submit_dag).
set -eu -o pipefail
shopt -s extglob

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODTESTS="$(cd "$HERE/.." && pwd)"

INDIRS=(); OUTDIR=""; WORKDIR=""; MERGE=0; PATTERN="*_nanoML.root"; STEP=500; KRB=0
NCPUS=2; MEMORY=""; DISK=2000000; FLAVOUR="longlunch"; RETRY=2; FORCE=0
METADATA=1; PROXY="${X509_USER_PROXY:-/tmp/x509up_u$(id -u)}"
LCG=/cvmfs/sft.cern.ch/lcg/views/LCG_109/x86_64-el9-gcc13-opt/setup.sh
TAG=""; SCHEDD=""; DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --indir)       INDIRS+=("$2"); shift 2 ;;
        --outdir)      OUTDIR="$2";   shift 2 ;;
        --workdir)     WORKDIR="$2";  shift 2 ;;
        --merge)       MERGE=1;       shift ;;
        --pattern)     PATTERN="$2";  shift 2 ;;
        --step-size)   STEP="$2";     shift 2 ;;
        --ncpus)       NCPUS="$2";    shift 2 ;;
        --memory)      MEMORY="$2";   shift 2 ;;
        --disk)        DISK="$2";     shift 2 ;;
        --flavour)     FLAVOUR="$2";  shift 2 ;;
        --retry)       RETRY="$2";    shift 2 ;;
        --force)       FORCE=1;       shift ;;
        --no-metadata) METADATA=0;    shift ;;
        --proxy)       PROXY="$2";    shift 2 ;;
        --lcg)         LCG="$2";      shift 2 ;;
        --tag)         TAG="$2";      shift 2 ;;
        --schedd)      SCHEDD="$2";   shift 2 ;;
        --dry-run)     DRY_RUN=1;     shift ;;
        -h|--help)     sed -n '2,44p' "$0"; exit 0 ;;
        --*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)             INDIRS+=("$1"); shift ;;
    esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
[ "${#INDIRS[@]}" -gt 0 ] && [ -n "$OUTDIR" ] && [ -n "$WORKDIR" ] || die "--indir, --outdir and --workdir are required"
[ "$MERGE" -eq 1 ] || [ "${#INDIRS[@]}" -eq 1 ] || die "several input directories need --merge"
for d in "${INDIRS[@]}"; do [ -d "$d" ] || die "input directory not found: $d"; done
[ -n "$TAG" ] || TAG="$(basename "${OUTDIR%/}")"

# ---- preflight ---------------------------------------------------------------
[ -z "${CMSSW_BASE:-}" ] || die "run this from a shell without cmsenv (CMSSW_BASE is set)"
[ -f "$LCG" ] || die "LCG setup script not found: $LCG"
command -v condor_submit_dag >/dev/null || [ "$DRY_RUN" -eq 1 ] || die "condor_submit_dag not in PATH"

REMOTE=0
case "$OUTDIR" in
    root://*)
        REMOTE=1
        case "$OUTDIR" in
            root://eoscms.cern.ch*|root://eosuser.cern.ch*|root://eoshome*.cern.ch*|root://eosproject*.cern.ch*)
                # CERN EOS accepts the kerberos ticket HTCondor forwards: no proxy shipped
                KRB=1
                klist -s 2>/dev/null || die "no valid kerberos ticket (kinit); HTCondor forwards it to the jobs"
                echo "Auth   : kerberos ($(klist 2>/dev/null | awk '/krbtgt/ {print "expires", $3, $4; exit}')), CERN EOS destination"
                ;;
            *)
                [ -f "$PROXY" ] || die "VOMS proxy not found at $PROXY (voms-proxy-init -voms cms --valid 192:00)"
                if command -v voms-proxy-info >/dev/null; then
                    LEFT=$(voms-proxy-info -file "$PROXY" -timeleft 2>/dev/null || echo 0)
                    [ "$LEFT" -gt 7200 ] || die "proxy $PROXY has only ${LEFT}s left; renew it (voms-proxy-init -voms cms --valid 192:00)"
                    echo "Proxy  : $PROXY ($((LEFT / 3600)) h left)"
                fi
                export X509_USER_PROXY="$PROXY"
                ;;
        esac
        XRD_HOST="${OUTDIR#root://}"; XRD_HOST="root://${XRD_HOST%%/*}"
        XRD_DIR="/${OUTDIR#root://*/}"; XRD_DIR="/${XRD_DIR##+(/)}"
        xrdfs "$XRD_HOST" mkdir -p "$XRD_DIR" >/dev/null 2>&1 || true
        xrdfs "$XRD_HOST" ls "$XRD_DIR" >/dev/null || die "cannot list $OUTDIR (proxy / permissions?)"
        ;;
    *)  mkdir -p "$OUTDIR" ;;
esac

# out_names DIR -> basenames of the files in output folder DIR (created if missing)
out_names() {
    case "$1" in
        root://*)
            local h p; h="${1#root://}"; h="root://${h%%/*}"; p="/${1#root://*/}"; p="/${p##+(/)}"
            xrdfs "$h" mkdir -p "$p" >/dev/null 2>&1 || true
            xrdfs "$h" ls "$p" 2>/dev/null | sed 's#.*/##' ;;
        *)  mkdir -p "$1"; ls "$1" 2>/dev/null ;;
    esac
}

# ---- conversion nodes --------------------------------------------------------
# NODE_*[i]: name, comma-separated inputs, output folder, output file name
NODE_NAME=(); NODE_IN=(); NODE_OUTDIR=(); NODE_OUTFILE=()
if [ "$MERGE" -eq 1 ]; then
    for d in "${INDIRS[@]}"; do
        mapfile -t files < <(ls -v "$d"/$PATTERN 2>/dev/null)
        [ "${#files[@]}" -gt 0 ] || die "no files matching $PATTERN in $d"
        ds="$(basename "${d%/}")"
        [ "$ds" = nanoML ] && ds="$(basename "$(dirname "${d%/}")")"
        # common prefix of the file names before _rep<N> (chain_common.sh TAG)
        prefix=""
        for f in "${files[@]}"; do
            b="$(basename "$f")"; b="${b%.root}"; b="${b%_nanoML}"; b="${b%_rep+([0-9])}"
            if [ -z "$prefix" ]; then prefix="$b"; elif [ "$prefix" != "$b" ]; then prefix="$ds"; break; fi
        done
        NODE_NAME+=("conv_${ds}")
        NODE_IN+=("$(IFS=,; echo "${files[*]}")")
        NODE_OUTDIR+=("${OUTDIR%/}/$ds")
        NODE_OUTFILE+=("${prefix}_split.parquet")
    done
else
    mapfile -t files < <(ls -v "${INDIRS[0]}"/$PATTERN 2>/dev/null)
    [ "${#files[@]}" -gt 0 ] || die "no files matching $PATTERN in ${INDIRS[0]}"
    for f in "${files[@]}"; do
        b="$(basename "$f")"; b="${b%.root}"; b="${b%_nanoML}"
        NODE_NAME+=("conv_${b}")
        NODE_IN+=("$f")
        NODE_OUTDIR+=("${OUTDIR%/}")
        NODE_OUTFILE+=("${b}_split.parquet")
    done
fi

# existing outputs (listed once per output folder)
declare -A LISTED
N_EXIST=0
echo "Nodes  : ${#NODE_NAME[@]} conversion job(s)$([ "$MERGE" -eq 1 ] && echo ' (--merge: one per input directory)')"
for i in "${!NODE_NAME[@]}"; do
    od="${NODE_OUTDIR[$i]}"
    [ -n "${LISTED[$od]+x}" ] || LISTED[$od]="$(out_names "$od" | tr '\n' ' ')"
    status="to do"
    case " ${LISTED[$od]} " in *" ${NODE_OUTFILE[$i]} "*)
        N_EXIST=$((N_EXIST + 1)); status=$([ "$FORCE" -eq 1 ] && echo "exists, will be overwritten" || echo "exists, will be skipped") ;;
    esac
    nin=$(tr ',' '\n' <<< "${NODE_IN[$i]}" | wc -l)
    [ "$MERGE" -eq 1 ] && echo "  $od/${NODE_OUTFILE[$i]}  <- $nin file(s)  [$status]"
done
[ "$MERGE" -eq 1 ] || echo "Output : $OUTDIR ($N_EXIST of ${#NODE_NAME[@]} outputs already there$([ "$FORCE" -eq 1 ] && echo ', will be overwritten' || echo ', will be skipped'))"

# ---- work directory ----------------------------------------------------------
mkdir -p "$WORKDIR/logs"
WORKDIR="$(cd "$WORKDIR" && pwd)"
case "$WORKDIR" in /eos/*) die "--workdir must not be on EOS (the schedds refuse /eos executables and logs); use AFS" ;; esac
cp "$HERE/dag.config" "$WORKDIR/dag.config"
# The node script must not be read from /eos by the schedd (see --workdir above).
RUNPARQUET="$WORKDIR/run_parquet.sh"
[ "$HERE/run_parquet.sh" -ef "$RUNPARQUET" ] || cp -f "$HERE/run_parquet.sh" "$RUNPARQUET"
JDL="parquet_${TAG}.jdl"; META_JDL="parquet_meta_${TAG}.jdl"; DAG="parquet_${TAG}.dag"

PROXY_ARG=""
if [ "$REMOTE" -eq 1 ] && [ "$KRB" -eq 0 ]; then
    # DAGMan runs condor_submit on the schedd, so the proxy must live on a
    # filesystem visible there (AFS), readable only by the owner.
    mkdir -p "$WORKDIR/proxy"
    if command -v fs >/dev/null && fs whichcell "$WORKDIR/proxy" >/dev/null 2>&1; then
        fs setacl -dir "$WORKDIR/proxy" -acl "$(id -un)" rlidwka system:anyuser none system:authuser none >/dev/null 2>&1 \
            || echo "WARNING: could not restrict the AFS ACL of $WORKDIR/proxy" >&2
    fi
    cp "$PROXY" "$WORKDIR/proxy/x509up"
    chmod 600 "$WORKDIR/proxy/x509up"
    PROXY_ARG="$WORKDIR/proxy/x509up"
fi

COMMON_ARGS="prodtests=$PRODTESTS lcg=$LCG"
{
    echo "# Auto-generated by submit_parquet.sh for $TAG"
    echo "universe                = vanilla"
    echo "executable              = $RUNPARQUET"
    echo "arguments               = stage=convert infiles=\$(infiles) outdir=\$(outdir) outfile=\$(outfile) step=$STEP force=$FORCE $COMMON_ARGS"
    echo "should_transfer_files   = YES"
    echo "when_to_transfer_output = ON_EXIT"
    echo "transfer_output_files   = \"\""
    [ -n "$PROXY_ARG" ] && echo "x509userproxy           = $PROXY_ARG"
    echo "output                  = logs/\$(jobname).out"
    echo "error                   = logs/\$(jobname).err"
    echo "log                     = logs/\$(jobname).log"
    echo "request_cpus            = $NCPUS"
    [ -n "$MEMORY" ] && echo "request_memory          = $MEMORY"
    echo "request_disk            = $DISK"
    echo "+JobFlavour             = \"$FLAVOUR\""
    echo "MY.WantOS               = \"el9\""
    echo "queue"
} > "$WORKDIR/$JDL"

{
    echo "# Auto-generated by submit_parquet.sh for $TAG: _metadata/_common_metadata writer"
    echo "universe                = vanilla"
    echo "executable              = $RUNPARQUET"
    echo "arguments               = stage=metadata outdir=\$(outdir) $COMMON_ARGS"
    echo "should_transfer_files   = YES"
    echo "when_to_transfer_output = ON_EXIT"
    echo "transfer_output_files   = \"\""
    [ -n "$PROXY_ARG" ] && echo "x509userproxy           = $PROXY_ARG"
    echo "output                  = logs/\$(jobname).out"
    echo "error                   = logs/\$(jobname).err"
    echo "log                     = logs/\$(jobname).log"
    echo "request_cpus            = 1"
    echo "request_disk            = 500000"
    echo "+JobFlavour             = \"espresso\""
    echo "MY.WantOS               = \"el9\""
    echo "queue"
} > "$WORKDIR/$META_JDL"

{
    echo "# Auto-generated DAG: endcap-split parquet conversion ($([ "$MERGE" -eq 1 ] && echo 'one file per input directory' || echo 'one file per nanoML file'))"
    echo "# inputs=${INDIRS[*]} pattern=$PATTERN outdir=$OUTDIR step=$STEP force=$FORCE"
    echo
    echo "CONFIG dag.config"
    echo
    for i in "${!NODE_NAME[@]}"; do
        echo "JOB  ${NODE_NAME[$i]} $JDL"
        echo "VARS ${NODE_NAME[$i]} infiles=\"${NODE_IN[$i]}\" outdir=\"${NODE_OUTDIR[$i]}\" outfile=\"${NODE_OUTFILE[$i]}\" jobname=\"${NODE_NAME[$i]}\""
        echo "RETRY ${NODE_NAME[$i]} $RETRY"
        echo
    done
    if [ "$METADATA" -eq 1 ]; then
        echo "# folder-level _metadata, once every conversion into that folder has succeeded"
        for od in "${!LISTED[@]}"; do
            mname="meta_$(basename "$od")"
            parents=()
            for i in "${!NODE_NAME[@]}"; do [ "${NODE_OUTDIR[$i]}" = "$od" ] && parents+=("${NODE_NAME[$i]}"); done
            echo "JOB  $mname $META_JDL"
            echo "VARS $mname outdir=\"$od\" jobname=\"$mname\""
            echo "RETRY $mname 1"
            echo "PARENT ${parents[*]} CHILD $mname"
            echo
        done
    fi
} > "$WORKDIR/$DAG"

echo "DAG    : $WORKDIR/$DAG (${#NODE_NAME[@]} conversion node(s)$([ "$METADATA" -eq 1 ] && echo " + ${#LISTED[@]} metadata node(s)"))"
echo "Jobs   : $NCPUS cpu(s)$([ -n "$MEMORY" ] && echo ", $MEMORY MB"), $DISK KB disk, flavour $FLAVOUR, retry $RETRY"

SCHEDD_ARG=()
[ -n "$SCHEDD" ] && SCHEDD_ARG=(-remote "$SCHEDD")
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: not submitting. To submit:"
    echo "  cd $WORKDIR && env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH condor_submit_dag ${SCHEDD_ARG[*]:-} -f $DAG"
    exit 0
fi

cd "$WORKDIR"
env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH condor_submit_dag "${SCHEDD_ARG[@]}" -f "$DAG"
echo
echo "Monitor with:"
echo "  condor_q -dag $(id -un)"
echo "  tail -f $WORKDIR/$DAG.dagman.out"
if [ "$REMOTE" -eq 1 ]; then
    echo "  xrdfs ${XRD_HOST#root://} ls -l $XRD_DIR"
else
    echo "  ls -l $OUTDIR"
fi
echo "Resubmit (only the missing outputs are redone):"
echo "  cd $WORKDIR && condor_submit_dag -f $DAG"
