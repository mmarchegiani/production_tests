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
# The gun reaches down to 0.2 GeV, so the two release thresholds that would hide the
# low-momentum pions are lowered as in Reco2Pico (gun_options.apply_gun_thresholds):
# CaloParticle creation E > 0.1 GeV (release 0.5) and no pT cut on the HGCalGenPart
# nano table (release pT > 0.5, i.e. |p| > 1.3-4.6 GeV in the endcap).
# --cpMinE / --genPtMin change them.
# Output files are tagged pions_stdCalo[_PU<N>]_flatRandom_*.
#
# Usage:   bash generate_pions_noFineCalo_flatRandom.sh <output_directory> [n_events] [options]
# Example: bash generate_pions_noFineCalo_flatRandom.sh /path/to/output/folder 1000 --pMin 1 --pMax 50 --seed 7
# Run with --help for the full option list.

PARTICLE=211   # pi+
PARTNAME="pions"
USE_FINE_CALO=0
GUN=flatRandom
CP_MIN_E=0.1
GEN_PT_MIN=0

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/chain_common.sh"

run_chain "$@"
