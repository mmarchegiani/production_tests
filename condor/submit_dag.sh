#!/bin/bash
# submit_dag.sh — one-shot submission wrapper for the pre4_D128 pipeline.
#
# The site is detected from the hostname (override with --site cern|lpc):
#   cern  lxplus/HTCondor. The CMSSW area is used in place from AFS (no
#         tarball), HTCondor forwards the kerberos ticket (no grid proxy),
#         outputs go to /eos/... via xrdcp. Default --mode chain: one job per
#         replica running GSD -> RECO -> nanoML, keeping only the nanoML file
#         (see build_dag.sh --keep).
#   lpc   cmslpc/HTCondor. A tarball of the CMSSW area (built once, cached)
#         is shipped with every job, a VOMS proxy is required, outputs go to
#         /store/user/... on cmseos. Default --mode stages: one job per stage
#         and replica, every stage output kept.
#
# Run from a shell where cmsenv has NOT been sourced (it leaks PYTHONHOME /
# PYTHONPATH / LD_LIBRARY_PATH, which breaks condor_submit_dag).
#
# Usage:
#   bash submit_dag.sh --nreplicas N --nevents N (--energy E | --minE E --maxE E) \
#        [--particle 22] [--partname photon] [--fine-calo] [--pileup N] [--nminbias N] \
#        [--gun flatEta|closeBy] [--nparticles N] [--delta CM] [--ticl] \
#        [--outdir PATH] [--workdir DIR] [--cmssw-base DIR] [--mode chain|stages] \
#        [--keep nano,reco,gsd] [--canary N] [--flavour workday] [--ncpus N] \
#        [--memory MB] [--nthreads N] [--seed-base N] [--retry N] \
#        [--schedd NAME] [--rebuild-tarball] [--dry-run] [--no-write-test]
#   Every build_dag.sh option is accepted and passed through; the last line
#   holds the wrapper-only options. --no-write-test skips both the 1 MB write
#   probe of --outdir and the EOS quota check (free space vs. the estimated
#   size of the kept outputs). --workdir is where the DAG, submit file
#   and logs are written (default: this directory) — it must not be on EOS.
#
# Examples:
#   # LPC smoke test: 1 replica, 2 events at 10 GeV
#   bash submit_dag.sh --energy 10 --nreplicas 1 --nevents 2
#
#   # CERN: 50k taus flat in 20-200 GeV, 100 jobs x 500 events, nanoML only,
#   # gated by a 5-event canary job
#   bash submit_dag.sh --particle 15 --partname taus --minE 20 --maxE 200 \
#        --nreplicas 100 --nevents 500 --canary 5 \
#        --cmssw-base /afs/cern.ch/work/u/user/CMSSW_20_1_0_pre4 \
#        --outdir /eos/user/u/user/taus_50k \
#        --workdir /afs/cern.ch/user/u/user/condor/taus_50k

set -eu -o pipefail

SITE=""; OUTDIR=""; CMSSW_BASE_ARG=""; WORKDIR=""
PILEUP=0; MODE=""; KEEP="nano"; NREPLICAS=""; NEVENTS=""
SCHEDD=""; REBUILD_TAR=0; DRY_RUN=0; WRITE_TEST=1
PASS=()

die() { echo "ERROR: $*" >&2; exit 1; }

# ---- Parse args ----------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --schedd)          SCHEDD="$2";         shift 2 ;;
        --rebuild-tarball) REBUILD_TAR=1;       shift ;;
        --dry-run)         DRY_RUN=1;           shift ;;
        --no-write-test)   WRITE_TEST=0;        shift ;;
        --site)            SITE="$2";           shift 2 ;;
        --outdir)          OUTDIR="$2";         shift 2 ;;
        --cmssw-base)      CMSSW_BASE_ARG="$2"; shift 2 ;;
        --workdir)         WORKDIR="$2";        shift 2 ;;
        --pileup)          PILEUP="$2"; PASS+=("$1" "$2"); shift 2 ;;
        --nreplicas)       NREPLICAS="$2"; PASS+=("$1" "$2"); shift 2 ;;
        --nevents)         NEVENTS="$2";   PASS+=("$1" "$2"); shift 2 ;;
        --mode)            MODE="$2";   PASS+=("$1" "$2"); shift 2 ;;
        --keep)            KEEP="$2";   PASS+=("$1" "$2"); shift 2 ;;
        --fine-calo)       PASS+=("$1"); shift ;;
        --ticl)            PASS+=("$1"); shift ;;
        -h|--help)
            grep '^#' "$0" | head -40
            exit 0
            ;;
        --*)
            [ $# -ge 2 ] || die "missing value for $1"
            PASS+=("$1" "$2"); shift 2 ;;
        *)
            die "unknown arg: $1" ;;
    esac
done

# ---- Site ----------------------------------------------------------------
if [ -z "$SITE" ]; then
    case "$(hostname -f 2>/dev/null)" in
        *.fnal.gov) SITE=lpc ;;
        *.cern.ch)  SITE=cern ;;
        *) die "cannot detect the site from the hostname; pass --site cern|lpc" ;;
    esac
fi
case "$SITE" in cern|lpc) ;; *) die "--site must be cern or lpc" ;; esac
[ -n "$MODE" ] || { [ "$SITE" = cern ] && MODE=chain || MODE=stages; }

# ---- Locate directories --------------------------------------------------
SUBMIT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRODTESTS_DIR="$(cd "$SUBMIT_DIR/.." && pwd)"
if [ -z "$CMSSW_BASE_ARG" ]; then
    # Default layout: $CMSSW_BASE/src/production_tests/condor
    CMSSW_BASE_ARG="$(cd "$PRODTESTS_DIR/../.." && pwd)"
fi
[ -d "$CMSSW_BASE_ARG/src" ] && [ -d "$CMSSW_BASE_ARG/lib" ] \
    || die "$CMSSW_BASE_ARG does not look like a CMSSW release (pass --cmssw-base)"
CMSSW_NAME="$(basename "$CMSSW_BASE_ARG")"

[ -n "$WORKDIR" ] || WORKDIR="$SUBMIT_DIR"
mkdir -p "$WORKDIR/logs"
WORKDIR="$(cd "$WORKDIR" && pwd)"
case "$WORKDIR" in /eos/*) die "--workdir must not be on EOS (HTCondor logs cannot live there)" ;; esac

if [ -z "$OUTDIR" ]; then
    if [ "$SITE" = lpc ]; then
        OUTDIR="/store/user/${USER}/production_tests/pre4_D128"
    else
        OUTDIR="/eos/user/${USER:0:1}/${USER}/production_tests/pre4_D128"
    fi
fi

echo "site:          $SITE (mode=$MODE)"
echo "CMSSW area:    $CMSSW_BASE_ARG"
echo "prod. tests:   $PRODTESTS_DIR"
echo "work dir:      $WORKDIR"
echo "output base:   $OUTDIR"
echo "pileup:        $PILEUP"
echo

# ---- Site preflight ------------------------------------------------------
TARBALL_ARGS=()
if [ "$SITE" = lpc ]; then
    TARBALL="${WORKDIR}/${CMSSW_NAME}.tar.gz"
    if [ "$REBUILD_TAR" -eq 1 ] || [ ! -f "$TARBALL" ]; then
        echo "Building tarball $TARBALL ..."
        ( cd "$(dirname "$CMSSW_BASE_ARG")" && \
          tar --exclude="${CMSSW_NAME}/tmp" \
              --exclude="${CMSSW_NAME}/logs" \
              --exclude="${CMSSW_NAME}/src/production_tests/condor" \
              --exclude="${CMSSW_NAME}/src/production_tests/parquet_out" \
              --exclude="${CMSSW_NAME}/src/production_tests/test*.root" \
              --exclude="${CMSSW_NAME}/src/production_tests/smoke*.root" \
              --exclude=".git" \
              -czf "$TARBALL" "${CMSSW_NAME}" )
        echo "Tarball: $(du -h "$TARBALL" | cut -f1)"
    else
        echo "Reusing existing tarball: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
        echo "(pass --rebuild-tarball to force refresh)"
    fi
    TARBALL_ARGS=(--tarball "$TARBALL")
    echo

    if [ -z "${X509_USER_PROXY:-}" ] || [ ! -f "$X509_USER_PROXY" ]; then
        echo "ERROR: X509_USER_PROXY not set or file missing" >&2
        echo "  Run: voms-proxy-init -voms cms -valid 192:00" >&2
        exit 1
    fi
    PROXY_HOURS=$(voms-proxy-info -timeleft 2>/dev/null | awk '{print int($1/3600)}')
    echo "Proxy at $X509_USER_PROXY (~${PROXY_HOURS} h left)"
    echo
else
    klist -s 2>/dev/null || die "no valid kerberos ticket (run kinit); HTCondor forwards it to the jobs"
    echo "Kerberos ticket OK: $(klist 2>/dev/null | awk '/krbtgt/ {print "expires", $3, $4; exit}')"
    # A forwarded, non-renewable ticket (klist -f flags without R) usually works,
    # but if condor_submit fails with "store_cred of Kerberos credential failed -
    # The credmon did not process credentials within the timeout period", a
    # fresh kinit (renewable ticket) is the first thing to try.
    klist -f 2>/dev/null | awk '/krbtgt/ {getline; print}' | grep -q 'Flags:.*R' \
        || echo "note: the TGT is not renewable (no R flag in klist -f); run kinit if condor_submit reports a credmon timeout"
    echo
fi

# ---- Output dirs + write test --------------------------------------------
# xrd_host_path <path> -> "host path" for remote areas, empty for local ones
xrd_host_path() {
    case "$1" in
        root://*)                local r="${1#root://}"; echo "${r%%/*} /${r#*/}" ;;
        /store/*)                echo "cmseos.fnal.gov $1" ;;
        /eos/cms/*)              echo "eoscms.cern.ch $1" ;;
        /eos/user/*|/eos/home-*) echo "eosuser.cern.ch $1" ;;
        /eos/project/*)          echo "eosproject.cern.ch $1" ;;
        *)                       echo "" ;;
    esac
}

if [ "$MODE" = chain ]; then
    SUBDIRS="nanoML"
    case ",$KEEP," in *",reco,"*)    SUBDIRS="RECO $SUBDIRS" ;; esac
    case ",$KEEP," in *",gsd,"*)     SUBDIRS="GSD $SUBDIRS" ;; esac
    case ",$KEEP," in *",minbias,"*) SUBDIRS="MINBIAS $SUBDIRS" ;; esac
else
    SUBDIRS="GSD RECO nanoML"
    [ "$PILEUP" -gt 0 ] && SUBDIRS="MINBIAS $SUBDIRS"
fi
echo "Ensuring output dirs exist under ${OUTDIR}/{${SUBDIRS// /,}}"
read -r XHOST XPATH <<< "$(xrd_host_path "$OUTDIR")"
for sub in $SUBDIRS; do
    if [ -n "$XHOST" ]; then
        xrdfs "$XHOST" mkdir -p "${XPATH}/${sub}" 2>/dev/null || true
    else
        mkdir -p "${OUTDIR}/${sub}"
    fi
done
if [ "$WRITE_TEST" -eq 1 ]; then
    # Catch unwritable areas / exhausted quotas before 100 jobs do.
    PROBE="${TMPDIR:-/tmp}/write_test_$$.bin"
    head -c 1048576 /dev/zero > "$PROBE"
    if [ -n "$XHOST" ]; then
        TARGET="${XPATH}/.write_test_$$"
        if xrdcp -f -s "$PROBE" "root://${XHOST}/${TARGET}"; then
            xrdfs "$XHOST" rm "$TARGET" >/dev/null 2>&1 || true
        else
            rm -f "$PROBE"
            die "cannot write a 1 MB test file to ${OUTDIR} (quota exhausted or no write permission)"
        fi
    else
        cp -f "$PROBE" "${OUTDIR}/.write_test_$$" || { rm -f "$PROBE"; die "cannot write to ${OUTDIR}"; }
        rm -f "${OUTDIR}/.write_test_$$"
    fi
    rm -f "$PROBE"
    echo "Write test to ${OUTDIR}: OK"
fi

# ---- EOS quota check (CERN EOS instances only) ---------------------------
# Rough per-event output sizes in MB, from the 10k-event tau runs (Sep 2026):
# nanoML 0.15, GSD 1.2, RECO 1.5 (no PU). Slightly inflated below for margin.
# With pileup (measured 2026-10-26 at PU 30 with a 1000-event minbias library,
# taus 20-200 GeV): GSD 26, RECO 31, nanoML 3.8, minbias library 1.9 MB/event;
# they grow roughly linearly with the pileup, so the PU 30 numbers are scaled.
if [ "$WRITE_TEST" -eq 1 ] && [ -n "$XHOST" ] && [ -n "$NREPLICAS" ] && [ -n "$NEVENTS" ] \
   && command -v eos >/dev/null 2>&1; then
    case "$XHOST" in *.cern.ch)
        PER_EVENT_MB=0
        PUSCALE=$(echo "$PILEUP / 30" | bc -l)
        for sub in $SUBDIRS; do
            if [ "$PILEUP" -gt 0 ]; then
                case "$sub" in
                    nanoML)  PER_EVENT_MB=$(echo "$PER_EVENT_MB + 4.0 * $PUSCALE" | bc -l) ;;
                    GSD)     PER_EVENT_MB=$(echo "$PER_EVENT_MB + 28 * $PUSCALE" | bc -l) ;;
                    RECO)    PER_EVENT_MB=$(echo "$PER_EVENT_MB + 33 * $PUSCALE" | bc -l) ;;
                    MINBIAS) PER_EVENT_MB=$(echo "$PER_EVENT_MB + 2.0" | bc -l) ;;
                esac
            else
                case "$sub" in
                    nanoML)  PER_EVENT_MB=$(echo "$PER_EVENT_MB + 0.2" | bc -l) ;;
                    GSD)     PER_EVENT_MB=$(echo "$PER_EVENT_MB + 1.3" | bc -l) ;;
                    RECO)    PER_EVENT_MB=$(echo "$PER_EVENT_MB + 1.6" | bc -l) ;;
                    MINBIAS) PER_EVENT_MB=$(echo "$PER_EVENT_MB + 0.5" | bc -l) ;;
                esac
            fi
        done
        NEED_GB=$(printf "%.1f" "$(echo "$NREPLICAS * $NEVENTS * $PER_EVENT_MB / 1000" | bc -l)")
        QUOTA=$(eos "root://${XHOST}" quota "$XPATH" 2>/dev/null | awk '
            function gb(v, u) { if (u == "TB") return v*1000; if (u == "GB") return v; if (u == "MB") return v/1000; if (u == "kB" || u == "KB") return v/1e6; if (u == "B") return v/1e9; return 0 }
            /By group/ { ingroup = 1; next }
            ingroup && $15 == "%" { printf "%.2f %.1f %s\n", $14, gb($10,$11) - gb($4,$5), ($16 ~ /exceeded/ ? "exceeded" : "ok"); exit }')
        if [ -n "$QUOTA" ]; then
            read -r Q_FILLED Q_FREE_GB Q_STATUS <<< "$QUOTA"
            echo "EOS quota for ${OUTDIR}: ${Q_FILLED}% used, ${Q_FREE_GB} GB free (logical); this campaign needs ~${NEED_GB} GB (${SUBDIRS// /,})"
            [ "$PILEUP" -gt 0 ] && echo "note: per-event sizes scaled from PU 30 taus (nanoML 3.8, GSD 26, RECO 31 MB/event)"
            # EOS flags the volume "exceeded" well before the logical quota is
            # full (physical replicas); the write probe above is authoritative,
            # so only the free-vs-needed comparison blocks the submission.
            [ "$Q_STATUS" = exceeded ] && echo "note: EOS reports the quota node as '$Q_STATUS'"
            if [ "$(echo "$Q_FREE_GB < $NEED_GB" | bc -l)" -eq 1 ]; then
                MSG="not enough EOS quota under ${OUTDIR} (${Q_FREE_GB} GB free, ~${NEED_GB} GB needed): free space or pick another --outdir (--no-write-test skips this check)"
                if [ "$DRY_RUN" -eq 1 ]; then echo "WARNING: $MSG"; else die "$MSG"; fi
            fi
        else
            echo "(could not parse 'eos quota' output for ${OUTDIR}; skipping the quota check)"
        fi
        ;;
    esac
fi
echo

# ---- Build DAG -----------------------------------------------------------
BUILD_OUT=$(bash "$SUBMIT_DIR/build_dag.sh" \
    --site "$SITE" --mode "$MODE" --outdir "$OUTDIR" --workdir "$WORKDIR" \
    --cmssw-base "$CMSSW_BASE_ARG" --prodtests "$PRODTESTS_DIR" \
    "${TARBALL_ARGS[@]}" "${PASS[@]}" | tee /dev/stderr)
DAG=$(printf '%s\n' "$BUILD_OUT" | sed -n 's/^Wrote \(.*\.dag\) with.*/\1/p')
[ -n "$DAG" ] && [ -f "$DAG" ] || die "build_dag.sh did not produce a DAG"
DAG_REL="$(basename "$DAG")"

# ---- Submit --------------------------------------------------------------
echo
SCHEDD_ARG=()
[ -n "$SCHEDD" ] && SCHEDD_ARG=(-Remote "$SCHEDD")

cd "$WORKDIR"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: not submitting. To submit:"
    echo "  cd $WORKDIR && env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH condor_submit_dag ${SCHEDD_ARG[*]:-} -f $DAG_REL"
    exit 0
fi

echo "Submitting DAG $DAG_REL from $WORKDIR ..."
env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH \
    condor_submit_dag "${SCHEDD_ARG[@]}" -f "$DAG_REL"

echo
echo "Done. Monitor with:"
if [ -n "$SCHEDD" ]; then
    echo "  condor_q -name $SCHEDD -dag \$(whoami)"
else
    echo "  condor_q -dag \$(whoami)"
fi
echo "  tail -f ${WORKDIR}/${DAG_REL}.dagman.out"
echo "  (per-job logs in ${WORKDIR}/logs/)"
echo
echo "Outputs land at:"
echo "  ${OUTDIR}/{${SUBDIRS// /,}}/"
echo
echo "Resume after failures (uses the rescue DAG):"
echo "  cd $WORKDIR && condor_submit_dag -f $DAG_REL"
