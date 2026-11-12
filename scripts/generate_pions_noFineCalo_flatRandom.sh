#!/bin/bash
#
# Full pi+ production chain with the flatRandom gun: [MINBIAS] -> GSD -> RECO -> nanoML -> parquet.
# useFineCalo=0 (standard calorimeter truth), no pileup by default.
#
# The gun is FlatRandomMultiParticlePGunProducer, configured as Reco2Pico's
# SingleHadronPGun_cfi.py: ONE pi+ per event from the origin, flat in |p|
# (default 0.2-200 GeV, E = sqrt(p^2 + m^2)) and in eta (default 1.6-2.9, a single
# interval, so only the +z endcap; --etaMin -2.9 --etaMax -1.6 for -z). The -z
# endcap stays empty, so keep the default (non-split) parquet.
# Output files are tagged pions_stdCalo[_PU<N>]_flatRandom_*.
#
# Usage:   bash generate_pions_noFineCalo_flatRandom.sh <output_directory> [n_events] [options]
# Example: bash generate_pions_noFineCalo_flatRandom.sh /path/to/output/folder 1000 --pMin 1 --pMax 50 --seed 7
# Run with --help for the full option list.

PARTICLE=211   # pi+
PARTNAME="pions"
USE_FINE_CALO=0
GUN=flatRandom

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/chain_common.sh"

run_chain "$@"
