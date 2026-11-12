#!/bin/bash
# run_stage.sh — one node of the pre4_D128 production DAG (see build_dag.sh).
#
# All arguments are key=value pairs, in any order:
#
#   stage=STAGE      minbias | gsd | reco | nano | chain
#                    chain = [minbias ->] gsd -> reco -> nano inside ONE job; the
#                    intermediate files stay in the job sandbox (see keep=)
#   particle=PDGID   22 (photon), 15 (tau), ...
#   partname=NAME    used in file names (photon, taus, ...)
#   minE=X maxE=X    gun energy range in GeV, flat (minE=maxE for a fixed energy)
#   energy=X         shorthand for minE=maxE=X
#   seed=N           GSD random seed (also the first luminosity block)
#   replica=N        replica index -> file-name suffix rep<N>; a non-numeric
#                    value (e.g. "canary") is used verbatim
#   nevents=N        signal events to generate
#   outdir=PATH      output base: /store/user/... (FNAL EOS), /eos/... (CERN EOS,
#                    staged out with xrdcp) or a plain shared-filesystem path
#   pileup=N         average PU (0 = none)
#   nminbias=N       minbias events to generate for the PU library (default nevents)
#   finecalo=0|1     useFineCalo for GSD_GUN.py (default 0)
#   gun=NAME         particle gun for GSD_GUN.py: flatEta (default) or closeBy
#   nparticles=N     particles per gun shot (GSD_GUN.py nParticles; default 1)
#   delta=X          closeBy gun only: separation in cm between consecutive particles
#                    on the HGCal front face (GSD_GUN.py delta; default 10, the
#                    producer's own). Rejected with gun=flatEta.
#   ticl=0|1         RECO.py useTICL: 1 = TICLv6 (ticl_v6 modifier: CLUEstering first
#                    iteration, Interpretations final tracksters), which the default
#                    nanoTruth_cfg.py requires; 0 = TICLv5 (default)
#   nthreads=N       threads for every cmsRun step (default 1)
#   keep=LIST        chain only: comma-separated subset of minbias,gsd,reco,nano
#                    to stage out (default nano)
#   cmssw=PATH       CMSSW release area to use directly when it is visible from
#                    the worker node (e.g. on AFS at CERN). When unset or not
#                    visible, a CMSSW_*.tar.gz from the job sandbox is unpacked
#                    instead (LPC mode).
#   prodtests=PATH   directory holding the cmsRun configs
#                    (default $CMSSW_BASE/src/production_tests)
#
# Outputs land in <outdir>/{MINBIAS,GSD,RECO,nanoML}/ as
#   <partname>_<stdCalo|fineCalo>[_PU<N>]_E<min>[to<max>][_closeBy_n<N>_d<delta>cm|_n<N>][_ticlv6]_rep<N>_<STEP>.root
# (the closeBy tag carries nparticles and delta with '.' written as 'p', e.g. d0p5cm;
# flatEta adds _n<N> only when nparticles != 1; _ticlv6 marks ticl=1).
set -eu -o pipefail

STAGE=""; PARTICLE=""; PARTNAME=""; MINE=20.0; MAXE=200.0; ENERGY=""
SEED=""; REPLICA=""; NEVENTS=""; OUTDIR=""; PILEUP=0; NMINBIAS=""
FINECALO=0; GUN=flatEta; NTHREADS=1; KEEP="nano"; CMSSW=""; PRODTESTS=""
NPARTICLES=1; DELTA=""; TICL=0

for kv in "$@"; do
    key="${kv%%=*}"; val="${kv#*=}"
    case "$key" in
        stage)      STAGE="$val" ;;
        particle)   PARTICLE="$val" ;;
        partname)   PARTNAME="$val" ;;
        energy)     ENERGY="$val" ;;
        minE|mine)  MINE="$val" ;;
        maxE|maxe)  MAXE="$val" ;;
        seed)       SEED="$val" ;;
        replica)    REPLICA="$val" ;;
        nevents)    NEVENTS="$val" ;;
        outdir)     OUTDIR="$val" ;;
        pileup)     PILEUP="$val" ;;
        nminbias)   NMINBIAS="$val" ;;
        finecalo)   FINECALO="$val" ;;
        gun)        GUN="$val" ;;
        nparticles) NPARTICLES="$val" ;;
        delta)      DELTA="$val" ;;
        ticl)       TICL="$val" ;;
        nthreads)   NTHREADS="$val" ;;
        keep)       KEEP="$val" ;;
        cmssw)      CMSSW="$val" ;;
        prodtests)  PRODTESTS="$val" ;;
        *) echo "ERROR: unknown argument '$kv'" >&2; exit 1 ;;
    esac
done

for var in STAGE PARTICLE PARTNAME SEED REPLICA NEVENTS OUTDIR; do
    [ -n "${!var}" ] || { echo "ERROR: $(echo "$var" | tr A-Z a-z)= is required" >&2; exit 1; }
done
if [ -n "$ENERGY" ]; then MINE="$ENERGY"; MAXE="$ENERGY"; fi
[ -n "$NMINBIAS" ] || NMINBIAS="$NEVENTS"
case "$GUN" in
    flatEta) [ -z "$DELTA" ] || { echo "ERROR: delta= applies to gun=closeBy only" >&2; exit 1; } ;;
    closeBy) [ -n "$DELTA" ] || DELTA=10 ;;
    *) echo "ERROR: gun must be flatEta or closeBy (got '$GUN')" >&2; exit 1 ;;
esac
case "$TICL" in 0|1) ;; *) echo "ERROR: ticl must be 0 or 1 (got '$TICL')" >&2; exit 1 ;; esac
# pepr's SimClusterMerger (hgcSimTruth -> MergedSimCluster tables) is not pileup-aware
# (it throws on the first pileup SimCluster), so nanoML drops it for PU samples.
RUN_MERGED_SC=1; [ "$PILEUP" -gt 0 ] && RUN_MERGED_SC=0

# ---- naming (must match build_dag.sh) -----------------------------------
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
case "$REPLICA" in
    ''|*[!0-9]*) REPTAG="$REPLICA" ;;
    *)           REPTAG="rep${REPLICA}" ;;
esac
TAG="${SAMPLE}_${REPTAG}"
MINBIAS_TAG="minbias_${SAMPLE}_${REPTAG}"
# Minbias uses a different seed offset to avoid colliding with the signal seed.
MINBIAS_SEED=$((SEED + 500000))

MINBIAS_OUT="${MINBIAS_TAG}_GENSIM.root"
GSD_OUT="${TAG}_GSD.root"
RECO_OUT="${TAG}_RECO.root"
DQM_OUT="${TAG}_DQM.root"
NANO_OUT="${TAG}_nanoML.root"

echo "=== $(hostname) $(date -u +%FT%TZ) STAGE=$STAGE TAG=$TAG ==="
echo "    particle=$PARTICLE  E=[$MINE,$MAXE] GeV  seed=$SEED  nevents=$NEVENTS  pileup=$PILEUP  nminbias=$NMINBIAS"
echo "    finecalo=$FINECALO  gun=$GUN  nparticles=$NPARTICLES  delta=${DELTA:-n/a}  ticl=$TICL  nthreads=$NTHREADS  keep=$KEEP"
echo "    outdir=$OUTDIR"
echo "    sandbox=$PWD"
echo

# ---- CMSSW environment -----------------------------------------------------
source /cvmfs/cms.cern.ch/cmsset_default.sh
if [ -n "$CMSSW" ] && [ -d "$CMSSW/src" ] && [ -d "$CMSSW/lib" ]; then
    echo "Using CMSSW area $CMSSW"
    pushd "$CMSSW/src" >/dev/null
    eval "$(scramv1 runtime -sh)"
    popd >/dev/null
else
    [ -z "$CMSSW" ] || echo "CMSSW area $CMSSW not visible from $(hostname); unpacking tarball"
    TARBALL=$(ls CMSSW_*.tar.gz 2>/dev/null | head -1)
    [ -n "$TARBALL" ] || { echo "ERROR: no CMSSW area and no CMSSW_*.tar.gz in the sandbox" >&2; ls; exit 1; }
    tar -xzf "$TARBALL"
    CMSSW_DIR=$(find . -maxdepth 1 -name "CMSSW_*" -type d | head -1)
    [ -n "$CMSSW_DIR" ] || { echo "ERROR: no CMSSW dir after unpacking $TARBALL" >&2; ls; exit 1; }
    pushd "$CMSSW_DIR/src" >/dev/null
    scram b ProjectRename 2>&1 | tail -3 || true
    eval "$(scramv1 runtime -sh)"
    popd >/dev/null
fi
CFG_DIR="${PRODTESTS:-$CMSSW_BASE/src/production_tests}"
[ -f "$CFG_DIR/GSD_GUN.py" ] || { echo "ERROR: cmsRun configs not found in $CFG_DIR" >&2; exit 1; }
echo "CMSSW_BASE=$CMSSW_BASE  SCRAM_ARCH=$SCRAM_ARCH  configs=$CFG_DIR"
echo

# ---- transfers ---------------------------------------------------------------
# xrd_url <path> -> xrootd URL for remote output areas, empty for local paths
xrd_url() {
    case "$1" in
        root://*)                 echo "$1" ;;
        /store/*)                 echo "root://cmseos.fnal.gov/$1" ;;
        /eos/cms/*)               echo "root://eoscms.cern.ch/$1" ;;
        /eos/user/*|/eos/home-*)  echo "root://eosuser.cern.ch/$1" ;;
        /eos/project/*)           echo "root://eosproject.cern.ch/$1" ;;
        *)                        echo "" ;;
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
# fetch <remote-or-local file> -> ./<basename>
fetch() {
    local src="$1" dst="./$(basename "$1")" url
    url=$(xrd_url "$src")
    if [ -n "$url" ]; then xrd_retry "$url" "$dst"; else cp -f "$src" "$dst"; fi
}
# stage_out <file> <dest dir>
stage_out() {
    local file="$1" dest="$2" url host path
    url=$(xrd_url "$dest")
    if [ -n "$url" ]; then
        host="${url#root://}"; host="${host%%/*}"
        path=$(echo "${url#root://$host}" | sed 's#^/*#/#')
        xrdfs "$host" mkdir -p "$path" 2>/dev/null || true
        xrd_retry "$file" "$url/$(basename "$file")"
    else
        mkdir -p "$dest"
        cp -f "$file" "$dest/$(basename "$file")"
    fi
    echo "staged out $file -> $dest/"
}
check_out() { [ -s "$1" ] || { echo "ERROR: $1 missing or empty" >&2; exit 2; }; }
keeps() { case ",${KEEP}," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

TIME=(); [ -x /usr/bin/time ] && TIME=(/usr/bin/time -v)

# ---- steps ---------------------------------------------------------------------
run_minbias() {
    echo "--- MINBIAS $(date -u +%FT%TZ): $NMINBIAS events, seed=$MINBIAS_SEED"
    "${TIME[@]}" cmsRun "$CFG_DIR/MINBIAS_GENSIM.py" \
        seed="$MINBIAS_SEED" maxEvents="$NMINBIAS" nThreads="$NTHREADS" \
        outputFile="$MINBIAS_OUT"
    check_out "$MINBIAS_OUT"
}
run_gsd() {
    echo "--- GSD $(date -u +%FT%TZ): $NEVENTS events, seed=$SEED"
    local args=(seed="$SEED" particle="$PARTICLE" minE="$MINE" maxE="$MAXE"
                useFineCalo="$FINECALO" gun="$GUN" nParticles="$NPARTICLES"
                maxEvents="$NEVENTS" nThreads="$NTHREADS"
                pileup="$PILEUP" outputFile="$GSD_OUT")
    [ -n "$DELTA" ] && args+=(delta="$DELTA")
    [ "$PILEUP" -gt 0 ] && args+=(pu="$MINBIAS_OUT")
    "${TIME[@]}" cmsRun "$CFG_DIR/GSD_GUN.py" "${args[@]}"
    check_out "$GSD_OUT"
}
run_reco() {
    echo "--- RECO $(date -u +%FT%TZ)"
    "${TIME[@]}" cmsRun "$CFG_DIR/RECO.py" nThreads="$NTHREADS" useTICL="$TICL" \
        inputFiles="file:${GSD_OUT}" outputFile="$RECO_OUT" outputFileDQM="$DQM_OUT"
    rm -f "$DQM_OUT"
    check_out "$RECO_OUT"
}
run_nano() {
    echo "--- nanoML $(date -u +%FT%TZ)"
    # NANO_CFG=nanoTruth_cfg.py selects the pepr-free config with the MC-truth-graph tables.
    "${TIME[@]}" cmsRun "$CFG_DIR/${NANO_CFG:-nanoTruth_cfg.py}" nThreads="$NTHREADS" runPFTruth=0 \
        runMergedSimClusters="$RUN_MERGED_SC" inputFiles="file:${RECO_OUT}" outputFile="$NANO_OUT"
    check_out "$NANO_OUT"
}

case "$STAGE" in
    minbias)
        run_minbias
        stage_out "$MINBIAS_OUT" "${OUTDIR}/MINBIAS"
        ;;
    gsd)
        if [ "$PILEUP" -gt 0 ]; then fetch "${OUTDIR}/MINBIAS/${MINBIAS_OUT}"; fi
        run_gsd
        rm -f "$MINBIAS_OUT"
        stage_out "$GSD_OUT" "${OUTDIR}/GSD"
        ;;
    reco)
        fetch "${OUTDIR}/GSD/${GSD_OUT}"
        run_reco
        rm -f "$GSD_OUT"
        stage_out "$RECO_OUT" "${OUTDIR}/RECO"
        ;;
    nano)
        fetch "${OUTDIR}/RECO/${RECO_OUT}"
        run_nano
        rm -f "$RECO_OUT"
        stage_out "$NANO_OUT" "${OUTDIR}/nanoML"
        ;;
    chain)
        if [ "$PILEUP" -gt 0 ]; then
            run_minbias
            keeps minbias && stage_out "$MINBIAS_OUT" "${OUTDIR}/MINBIAS"
        fi
        run_gsd
        rm -f "$MINBIAS_OUT"
        keeps gsd && stage_out "$GSD_OUT" "${OUTDIR}/GSD"
        run_reco
        rm -f "$GSD_OUT"
        keeps reco && stage_out "$RECO_OUT" "${OUTDIR}/RECO"
        run_nano
        rm -f "$RECO_OUT"
        stage_out "$NANO_OUT" "${OUTDIR}/nanoML"
        ;;
    *) echo "ERROR: bad stage '$STAGE'" >&2; exit 1 ;;
esac

echo
echo "sandbox usage before cleanup: $(du -sh . 2>/dev/null | cut -f1)"
rm -f "$MINBIAS_OUT" "$GSD_OUT" "$RECO_OUT" "$DQM_OUT" "$NANO_OUT"
echo "=== DONE $(date -u +%FT%TZ) ==="
