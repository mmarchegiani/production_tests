#!/bin/bash
#
# Full photons production chain: [MINBIAS] -> GSD -> RECO -> nanoML -> parquet.
# useFineCalo=0 (standard calorimeter truth: GSD_fragment / GSD_fragment_PU), no pileup by default.
#
# This is the standard (non-fine-calo) truth configuration; it is also the only
# one that supports pileup mixing, via --pileup N (a minbias library is
# generated with MINBIAS_GENSIM.py automatically unless --pu FILE is given).
#
# Usage:   bash generate_photons_noFineCalo.sh <output_directory> [n_events] [options]
# Example: bash generate_photons_noFineCalo.sh /path/to/output/folder 1000
# Run with --help for the full option list.

PARTICLE=22   # photon
PARTNAME="photons"
USE_FINE_CALO=0

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/chain_common.sh"

run_chain "$@"
