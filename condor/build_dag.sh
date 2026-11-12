#!/bin/bash
# build_dag.sh — generate a DAGMan .dag (plus its submit file) for the
# pre4_D128 pipeline. Normally called by submit_dag.sh.
#
# Two layouts (--mode):
#   chain   one node per replica running [minbias ->] gsd -> reco -> nano in a
#           SINGLE job; intermediate files stay in the job sandbox and only the
#           products listed in --keep are staged out (default: nano).
#           Default at CERN, where EOS quota is the limiting factor.
#   stages  one node per stage and replica ([minbias_repN ->] gsd_repN ->
#           reco_repN -> nano_repN); every stage output is staged out and
#           fetched back by the next stage. Default at LPC (original layout).
#
# Replicas are independent, so DAGMan parallelises them across the queue.
# --canary N adds a single chain node with N events that every replica
# depends on: a broken setup then fails once instead of N times.
#
# Usage:
#   bash build_dag.sh --nreplicas 10 --nevents 200 (--energy 100 | --minE 20 --maxE 200) \
#        [--particle 22] [--partname photon] [--fine-calo] [--gun flatEta|closeBy] \
#        [--nparticles 1] [--delta CM] [--ticl] \
#        [--outdir PATH] [--pileup 0] [--nminbias <nevents>] \
#        [--site cern|lpc] [--mode chain|stages] [--keep nano[,reco,gsd,minbias]] \
#        [--cmssw-base DIR] [--prodtests DIR] [--tarball FILE] \
#        [--flavour workday] [--ncpus N] [--memory MB] [--disk KB] [--nthreads 1] \
#        [--canary 0] [--seed-base N] [--retry 2] [--workdir DIR] [--outfile FILE.dag]
#
# --workdir (DAG, submit file, logs and a copy of run_stage.sh) must be on AFS at
# CERN: the schedds reject /eos executables and logs, while the CMSSW area and
# the outputs may be on EOS (they only appear in the job arguments).
#
# Seeds: replica i uses seed_base + i (minbias: + 500000). seed_base defaults
# to round(energy*1000) for a fixed energy and particle*100000 + round(minE*1000)
# for a flat range; the canary uses seed_base + nreplicas.
# File names: <partname>_<stdCalo|fineCalo>[_PU<N>]_E<min>[to<max>][_closeBy_n<N>_d<delta>cm|_n<N>][_ticlv6]_rep<i>_<STEP>.root
# (the same rule is implemented in run_stage.sh). --nparticles/--delta/--ticl are
# forwarded to run_stage.sh as nparticles=/delta=/ticl= (see its header).

set -eu -o pipefail

# ---- Defaults ------------------------------------------------------------
NREPLICAS=""; NEVENTS=""; ENERGY=""; MINE=""; MAXE=""
PARTICLE=22; PARTNAME=photon; FINECALO=0; GUN=flatEta
NPARTICLES=1; DELTA=""; TICL=0
OUTDIR=""; PILEUP=0; NMINBIAS=""
SITE=""; MODE=""; KEEP="nano"
CMSSW_BASE_ARG=""; PRODTESTS=""; TARBALL=""
FLAVOUR="workday"; NCPUS=""; MEMORY=""; DISK=20000000; NTHREADS=1
CANARY=0; SEED_BASE=""; RETRY=2; WORKDIR=""; OUTFILE=""

# ---- Parse args ----------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --nreplicas)   NREPLICAS="$2";      shift 2 ;;
        --nevents)     NEVENTS="$2";        shift 2 ;;
        --energy)      ENERGY="$2";         shift 2 ;;
        --minE|--mine) MINE="$2";           shift 2 ;;
        --maxE|--maxe) MAXE="$2";           shift 2 ;;
        --particle)    PARTICLE="$2";       shift 2 ;;
        --partname)    PARTNAME="$2";       shift 2 ;;
        --fine-calo)   FINECALO=1;          shift ;;
        --gun)         GUN="$2";            shift 2 ;;
        --nparticles)  NPARTICLES="$2";     shift 2 ;;
        --delta)       DELTA="$2";          shift 2 ;;
        --ticl)        TICL=1;              shift ;;
        --outdir)      OUTDIR="$2";         shift 2 ;;
        --pileup)      PILEUP="$2";         shift 2 ;;
        --nminbias)    NMINBIAS="$2";       shift 2 ;;
        --site)        SITE="$2";           shift 2 ;;
        --mode)        MODE="$2";           shift 2 ;;
        --keep)        KEEP="$2";           shift 2 ;;
        --cmssw-base)  CMSSW_BASE_ARG="$2"; shift 2 ;;
        --prodtests)   PRODTESTS="$2";      shift 2 ;;
        --tarball)     TARBALL="$2";        shift 2 ;;
        --flavour)     FLAVOUR="$2";        shift 2 ;;
        --ncpus)       NCPUS="$2";          shift 2 ;;
        --memory)      MEMORY="$2";         shift 2 ;;
        --disk)        DISK="$2";           shift 2 ;;
        --nthreads)    NTHREADS="$2";       shift 2 ;;
        --canary)      CANARY="$2";         shift 2 ;;
        --seed-base)   SEED_BASE="$2";      shift 2 ;;
        --retry)       RETRY="$2";          shift 2 ;;
        --workdir)     WORKDIR="$2";        shift 2 ;;
        --outfile)     OUTFILE="$2";        shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -32
            exit 0
            ;;
        *)
            echo "unknown arg: $1" >&2
            exit 1
            ;;
    esac
done

die() { echo "ERROR: $*" >&2; exit 1; }

# ---- Validate ------------------------------------------------------------
[ -n "$NREPLICAS" ] || die "--nreplicas is required"
[ -n "$NEVENTS" ]   || die "--nevents is required"
if [ -n "$ENERGY" ]; then MINE="$ENERGY"; MAXE="$ENERGY"; fi
[ -n "$MINE" ] && [ -n "$MAXE" ] || die "--energy, or both --minE and --maxE, is required"
[ "$FINECALO" -eq 1 ] && [ "$PILEUP" -gt 0 ] && die "--fine-calo does not support pileup (GSDfineCalo_fragment has no PU mixing)"
case "$GUN" in
    flatEta) [ -z "$DELTA" ] || die "--delta applies to --gun closeBy only" ;;
    closeBy) [ -n "$DELTA" ] || DELTA=10 ;;
    *) die "--gun must be flatEta or closeBy" ;;
esac
[[ "$NPARTICLES" =~ ^[1-9][0-9]*$ ]] || die "--nparticles must be a positive integer (got '$NPARTICLES')"
[ -n "$NMINBIAS" ] || NMINBIAS="$NEVENTS"

if [ -z "$SITE" ]; then
    case "$(hostname -f 2>/dev/null)" in
        *.fnal.gov) SITE=lpc ;;
        *.cern.ch)  SITE=cern ;;
        *) die "cannot detect the site from the hostname; pass --site cern|lpc" ;;
    esac
fi
case "$SITE" in cern|lpc) ;; *) die "--site must be cern or lpc" ;; esac
if [ -z "$MODE" ]; then
    [ "$SITE" = cern ] && MODE=chain || MODE=stages
fi
case "$MODE" in chain|stages) ;; *) die "--mode must be chain or stages" ;; esac
case ",$KEEP," in *",nano,"*) ;; *) KEEP="nano,$KEEP" ;; esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -n "$WORKDIR" ] || WORKDIR="$SCRIPT_DIR"
mkdir -p "$WORKDIR/logs"
WORKDIR="$(cd "$WORKDIR" && pwd)"
# The CERN schedds refuse a submit file that names /eos in executable, log,
# output, error, initialdir or transfer_input_files (SUBMIT_REQUIREMENT_NoEos);
# only the job arguments may carry /eos paths (cmssw=, prodtests=, outdir=).
# The repo itself may live on EOS, so the node script is copied next to the DAG
# (the work dir, which must be on AFS) and the submit file points at that copy.
if [ "$SITE" = cern ]; then
    case "$WORKDIR" in /eos/*) die "--workdir must not be on EOS at CERN (the schedds refuse /eos executables and logs); use the AFS home" ;; esac
fi
RUNSTAGE="$WORKDIR/run_stage.sh"
[ "$SCRIPT_DIR/run_stage.sh" -ef "$RUNSTAGE" ] || cp -f "$SCRIPT_DIR/run_stage.sh" "$RUNSTAGE"
[ -n "$PRODTESTS" ] || PRODTESTS="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$OUTDIR" ]; then
    if [ "$SITE" = lpc ]; then
        OUTDIR="/store/user/${USER}/production_tests/pre4_D128"
    else
        OUTDIR="/eos/user/${USER:0:1}/${USER}/production_tests/pre4_D128"
    fi
fi

# Software delivery: CERN runs the area in place (AFS is visible from the
# worker nodes); LPC ships a tarball (built by submit_dag.sh).
CMSSW_ARG=""; PRODTESTS_ARG=""
if [ "$SITE" = cern ]; then
    [ -n "$CMSSW_BASE_ARG" ] || die "--cmssw-base is required at CERN (the release area is used in place)"
    [ -d "$CMSSW_BASE_ARG/src" ] && [ -d "$CMSSW_BASE_ARG/lib" ] || die "$CMSSW_BASE_ARG is not a CMSSW release area"
    CMSSW_ARG="$CMSSW_BASE_ARG"
    PRODTESTS_ARG="$PRODTESTS"
else
    [ -n "$TARBALL" ] || die "--tarball is required at LPC"
fi
# GSD peaks at ~4.4 GB RSS (1 thread; ~5.2 GB with 2): above the 3 GB/core CERN
# slot default, hence 2 cores there; LPC takes an explicit request_memory.
[ -n "$NCPUS" ]  || { [ "$SITE" = cern ] && NCPUS=2 || NCPUS=1; }
[ -n "$MEMORY" ] || { [ "$SITE" = lpc ] && MEMORY=5000 || MEMORY=""; }

# ---- Naming and seeds (must match run_stage.sh) ---------------------------
fmt0() { printf "%.0f" "$1"; }
if [ "$(fmt0 "$MINE")" = "$(fmt0 "$MAXE")" ]; then
    ETAG="E$(fmt0 "$MINE")"
else
    ETAG="E$(fmt0 "$MINE")to$(fmt0 "$MAXE")"
fi
CALO="stdCalo"; [ "$FINECALO" -eq 1 ] && CALO="fineCalo"
SAMPLE="${PARTNAME}_${CALO}"
[ "$PILEUP" -gt 0 ] && SAMPLE="${SAMPLE}_PU${PILEUP}"
SAMPLE="${SAMPLE}_${ETAG}"
dtag() { printf "%g" "$1" | tr . p; }
if [ "$GUN" = closeBy ]; then
    SAMPLE="${SAMPLE}_closeBy_n${NPARTICLES}_d$(dtag "$DELTA")cm"
elif [ "$NPARTICLES" -ne 1 ]; then
    SAMPLE="${SAMPLE}_n${NPARTICLES}"
fi
[ "$TICL" -eq 1 ] && SAMPLE="${SAMPLE}_ticlv6"

if [ -z "$SEED_BASE" ]; then
    if [ -n "$ENERGY" ]; then
        SEED_BASE=$(printf "%.0f" "$(echo "$ENERGY * 1000" | bc -l)")
    else
        SEED_BASE=$(( PARTICLE * 100000 + $(printf "%.0f" "$(echo "$MINE * 1000" | bc -l)") ))
    fi
fi

[ -n "$OUTFILE" ] || OUTFILE="$WORKDIR/pipeline_${SAMPLE}.dag"
case "$OUTFILE" in /*) ;; *) OUTFILE="$WORKDIR/$OUTFILE" ;; esac
JDL="${OUTFILE%.dag}.jdl"
[ "$(dirname "$OUTFILE")" = "$WORKDIR" ] || die "--outfile must live in the work dir ($WORKDIR)"
[ -f "$WORKDIR/dag.config" ] || cp "$SCRIPT_DIR/dag.config" "$WORKDIR/dag.config"

# ---- Emit submit file ----------------------------------------------------
{
    echo "# Auto-generated by build_dag.sh for $SAMPLE (site=$SITE, mode=$MODE)"
    echo "universe                = vanilla"
    echo "executable              = ${RUNSTAGE}"
    echo "arguments               = stage=\$(stage) particle=${PARTICLE} partname=${PARTNAME} minE=${MINE} maxE=${MAXE} seed=\$(seed) replica=\$(replica) nevents=\$(nevents) outdir=\$(outdir) pileup=${PILEUP} nminbias=${NMINBIAS} finecalo=${FINECALO} gun=${GUN} nparticles=${NPARTICLES} delta=${DELTA} ticl=${TICL} nthreads=${NTHREADS} keep=${KEEP} cmssw=${CMSSW_ARG} prodtests=${PRODTESTS_ARG}"
    echo "should_transfer_files   = YES"
    echo "when_to_transfer_output = ON_EXIT"
    echo "transfer_output_files   = \"\""
    [ "$SITE" = lpc ] && echo "transfer_input_files    = ${TARBALL}"
    echo "output                  = logs/\$(jobname).out"
    echo "error                   = logs/\$(jobname).err"
    echo "log                     = logs/\$(jobname).log"
    echo "request_cpus            = ${NCPUS}"
    [ -n "$MEMORY" ] && echo "request_memory          = ${MEMORY}"
    echo "request_disk            = ${DISK}"
    if [ "$SITE" = lpc ]; then
        echo "+MaxRuntime             = 86400"
        echo "+DesiredOS              = \"rhel9\""
        echo "+REQUIRED_OS            = \"rhel9\""
        echo "x509userproxy           = \$ENV(X509_USER_PROXY)"
    else
        echo "+JobFlavour             = \"${FLAVOUR}\""
        echo "MY.WantOS               = \"el9\""
    fi
    echo "queue"
} > "$JDL"
JDL_REL="$(basename "$JDL")"

# ---- Emit DAG ------------------------------------------------------------
echo "Generating $OUTFILE"
echo "  sample=$SAMPLE  particle=$PARTICLE  E=[$MINE,$MAXE] GeV  fineCalo=$FINECALO  gun=$GUN  nparticles=$NPARTICLES  delta=${DELTA:-n/a}  ticl=$TICL"
echo "  nevents=$NEVENTS x nreplicas=$NREPLICAS  pileup=$PILEUP  nminbias=$NMINBIAS"
echo "  site=$SITE  mode=$MODE  keep=$KEEP  seed_base=$SEED_BASE  canary=$CANARY"
echo "  outdir=$OUTDIR"
echo "  workdir=$WORKDIR  submit file=$JDL_REL"
echo

FIRST_NODES=()
{
    echo "# Auto-generated DAG for pre4_D128 production_tests"
    echo "# sample=${SAMPLE}  particle=${PARTICLE}  minE=${MINE}  maxE=${MAXE}  fineCalo=${FINECALO}  gun=${GUN}  nparticles=${NPARTICLES}  delta=${DELTA:-n/a}  ticl=${TICL}"
    echo "# nreplicas=${NREPLICAS}  nevents=${NEVENTS}  pileup=${PILEUP}  nminbias=${NMINBIAS}"
    echo "# site=${SITE}  mode=${MODE}  keep=${KEEP}  seed_base=${SEED_BASE}  outdir=${OUTDIR}"
    echo ""
    echo "CONFIG dag.config"
    echo ""

    if [ "$CANARY" -gt 0 ]; then
        NC="canary_${SAMPLE}"
        echo "# Canary: full chain on ${CANARY} events; the replicas start only if it succeeds"
        echo "JOB  ${NC} ${JDL_REL}"
        echo "VARS ${NC} stage=\"chain\" seed=\"$((SEED_BASE + NREPLICAS))\" replica=\"canary\" nevents=\"${CANARY}\" outdir=\"${OUTDIR}/canary\" jobname=\"${NC}\""
        echo "RETRY ${NC} 1"
        echo ""
    fi

    for REP in $(seq 0 $((NREPLICAS - 1))); do
        SEED=$((SEED_BASE + REP))
        BASE_VARS="seed=\"${SEED}\" replica=\"${REP}\" nevents=\"${NEVENTS}\" outdir=\"${OUTDIR}\""

        if [ "$MODE" = chain ]; then
            N="chain_${SAMPLE}_rep${REP}"
            echo "JOB  ${N} ${JDL_REL}"
            echo "VARS ${N} stage=\"chain\" ${BASE_VARS} jobname=\"${N}\""
            echo "RETRY ${N} ${RETRY}"
            echo ""
            FIRST_NODES+=("$N")
            continue
        fi

        NMB="minbias_${SAMPLE}_rep${REP}"
        NGSD="gsd_${SAMPLE}_rep${REP}"
        NRECO="reco_${SAMPLE}_rep${REP}"
        NNANO="nano_${SAMPLE}_rep${REP}"

        if [ "$PILEUP" -gt 0 ]; then
            echo "JOB  ${NMB} ${JDL_REL}"
            echo "VARS ${NMB} stage=\"minbias\" ${BASE_VARS} jobname=\"${NMB}\""
            echo "RETRY ${NMB} ${RETRY}"
            echo ""
            FIRST_NODES+=("$NMB")
        else
            FIRST_NODES+=("$NGSD")
        fi
        for pair in "gsd:${NGSD}" "reco:${NRECO}" "nano:${NNANO}"; do
            st="${pair%%:*}"; nd="${pair#*:}"
            echo "JOB  ${nd} ${JDL_REL}"
            echo "VARS ${nd} stage=\"${st}\" ${BASE_VARS} jobname=\"${nd}\""
            echo "RETRY ${nd} ${RETRY}"
            echo ""
        done
        [ "$PILEUP" -gt 0 ] && echo "PARENT ${NMB} CHILD ${NGSD}"
        echo "PARENT ${NGSD} CHILD ${NRECO}"
        echo "PARENT ${NRECO} CHILD ${NNANO}"
        echo ""
    done

    if [ "$CANARY" -gt 0 ]; then
        echo "PARENT canary_${SAMPLE} CHILD ${FIRST_NODES[*]}"
        echo ""
    fi
} > "$OUTFILE"

NODES=$(grep -c '^JOB ' "$OUTFILE")
echo "Wrote $OUTFILE with $NODES nodes ($NREPLICAS replicas, mode=$MODE$([ "$CANARY" -gt 0 ] && echo ", + canary"))"
echo "Submit file: $JDL"
