#!/bin/bash
#
# Full photons production chain: [MINBIAS] -> GSD -> RECO -> nanoML -> parquet.
# useFineCalo=1 (fine calorimeter segmentation: GSDfineCalo_fragment), no pileup by default.
#
# Fine calo gives per-SimTrack calorimeter truth at the shower level, at the
# price of a larger GSD file and no pileup support (GSDfineCalo_fragment has no
# PU mixing wiring) -- see generate_photons_noFineCalo.sh for PU samples.
#
# Usage:   bash generate_photons_FineCalo.sh <output_directory> [n_events] [options]
# Example: bash generate_photons_FineCalo.sh /path/to/output/folder 1000
# Run with --help for the full option list.

PARTICLE=22   # photon
PARTNAME="photons"
USE_FINE_CALO=1

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/chain_common.sh"

run_chain "$@"
