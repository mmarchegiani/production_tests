import uproot
import numpy as np
import awkward as ak
from glob import glob
from tqdm import tqdm
from collections import defaultdict
import os, pathlib
import argparse
import gc
import pyarrow.parquet as pq
import pyarrow as pa


def parse_args():
    parser = argparse.ArgumentParser(
        description='Process nanoML root files (with TICL tables inline) into a single parquet file. '
                    'For CMSSW_20_0_0_pre1: TICL is now default reco, tracksters and TICLCandidates '
                    'live in the same Events tree as SimClusters/RecHits (no separate output_ticl.root).'
    )
    parser.add_argument('--nanoMLfiles', nargs='+', required=True, help='List of nanoML root files')
    parser.add_argument('--outputDir', type=str, default="parquet_out", help='Output directory for parquet files')
    parser.add_argument('--outputFile', type=str, default="output.parquet", help='Output parquet filename')
    parser.add_argument('--compression', type=str, default='lz4', help='Parquet compression algorithm')
    parser.add_argument('--batch_size', type=int, default=2, help='Number of files to process at once')

    return parser.parse_args()


# Branches grouped by object family. Prefixes match the nano flat table
# naming: e.g. "SimCluster_" branches all belong to the SimCluster object.
#
# Note: SimCluster_isPileup, MergedSimCluster_isPileup and
# MergedCaloTruthMergedSimCluster_isPileup are derived in process_batch() from
# the corresponding bunchCrossing/eventId branches (signal iff BX==0 AND
# eventId==0). They are not read from disk.
BRANCH_GROUPS = {
    "RecHitHGC": [
        'RecHitHGC_x', 'RecHitHGC_y', 'RecHitHGC_z',
        'RecHitHGC_energy', 'RecHitHGC_time',
        'RecHitHGC_detId',
        # Sim-truth matches
        'RecHitHGC_MergedSimClusterBestMatchIdx',
        'RecHitHGC_MergedSimClusterBestMatchQual',
        'RecHitHGC_SimClusterBestMatchIdx',
        'RecHitHGC_SimClusterBestMatchQual',
        'RecHitHGC_MergedCaloTruthMergedSimClusterBestMatchIdx',
        'RecHitHGC_MergedCaloTruthMergedSimClusterBestMatchQual',
        # LayerCluster match (starts the RecHit->LC->Trackster->TICLCand chain)
        'RecHitHGC_LayerCluster_MatchIdx',
        'RecHitHGC_LayerClusterNumMatch',
    ],
    "MergedSimCluster": [
        'MergedSimCluster_impactPoint_eta', 'MergedSimCluster_impactPoint_phi',
        'MergedSimCluster_impactPoint_x', 'MergedSimCluster_impactPoint_y',
        'MergedSimCluster_impactPoint_z',
        'MergedSimCluster_boundaryEnergy', 'MergedSimCluster_recEnergy',
        'MergedSimCluster_pdgId', 'MergedSimCluster_trackIdAtBoundary',
        # Signal/pileup discrimination: signal iff bunchCrossing==0 & eventId==0
        'MergedSimCluster_eventId', 'MergedSimCluster_bunchCrossing',
    ],
    "MergedCaloTruthMergedSimCluster": [
        'MergedCaloTruthMergedSimCluster_impactPoint_eta', 'MergedCaloTruthMergedSimCluster_impactPoint_phi',
        'MergedCaloTruthMergedSimCluster_impactPoint_x', 'MergedCaloTruthMergedSimCluster_impactPoint_y',
        'MergedCaloTruthMergedSimCluster_impactPoint_z',
        'MergedCaloTruthMergedSimCluster_boundaryEnergy', 'MergedCaloTruthMergedSimCluster_recEnergy',
        'MergedCaloTruthMergedSimCluster_pdgId', 'MergedCaloTruthMergedSimCluster_trackIdAtBoundary',
        'MergedCaloTruthMergedSimCluster_eventId', 'MergedCaloTruthMergedSimCluster_bunchCrossing',
    ],
    "SimCluster": [
        'SimCluster_impactPoint_eta', 'SimCluster_impactPoint_phi',
        'SimCluster_impactPoint_x', 'SimCluster_impactPoint_y',
        'SimCluster_impactPoint_z',
        'SimCluster_boundaryEnergy', 'SimCluster_recEnergy',
        'SimCluster_pdgId', 'SimCluster_trackIdAtBoundary',
        'SimCluster_eventId', 'SimCluster_bunchCrossing',
        'SimCluster_CaloPartIdx',
    ],
    "LayerCluster": [
        'LayerCluster_x', 'LayerCluster_y', 'LayerCluster_z',
        'LayerCluster_eta', 'LayerCluster_phi', 'LayerCluster_energy',
        'LayerCluster_nHits', 'LayerCluster_seedDetId',
        # Sim-truth matches
        'LayerCluster_CaloPart_MatchIdx', 'LayerCluster_CaloPart_MatchQual',
        'LayerCluster_CaloPartNumMatch',
        'LayerCluster_SimCluster_MatchIdx', 'LayerCluster_SimCluster_MatchQual',
        'LayerCluster_SimClusterNumMatch',
    ],
    "TICLCand": [
        'TICLCand_pt', 'TICLCand_p', 'TICLCand_energy', 'TICLCand_raw_energy',
        'TICLCand_eta', 'TICLCand_phi', 'TICLCand_mass',
        'TICLCand_pdgID', 'TICLCand_charge',
        'TICLCand_time', 'TICLCand_timeError',
    ],
    # TICLCand -> linked Tracksters (one-to-many). nCandidate2Tracksters is
    # per-event count; nCandidate2TrackstersIndices is total count of link
    # entries flattened; tracksterIndex is the flat index into the trackster
    # tables below.
    "Candidate2Tracksters": [
        'nCandidate2Tracksters',
        'nCandidate2TrackstersIndices',
        'Candidate2TrackstersIndices_tracksterIndex',
    ],
    # Trackster kinematic + LayerCluster association tables. `..vertices`
    # branches expose per-trackster lists of LayerCluster indices and the
    # energy fraction each LC contributes to the trackster (vertex_mult).
    "ticlTrackstersCLUE3DHigh": [
        'ticlTrackstersCLUE3DHigh_raw_energy', 'ticlTrackstersCLUE3DHigh_raw_em_energy',
        'ticlTrackstersCLUE3DHigh_raw_pt', 'ticlTrackstersCLUE3DHigh_regressed_energy',
        'ticlTrackstersCLUE3DHigh_barycenter_x', 'ticlTrackstersCLUE3DHigh_barycenter_y',
        'ticlTrackstersCLUE3DHigh_barycenter_z',
        'ticlTrackstersCLUE3DHigh_barycenter_eta', 'ticlTrackstersCLUE3DHigh_barycenter_phi',
        'ticlTrackstersCLUE3DHigh_time', 'ticlTrackstersCLUE3DHigh_timeError',
        'ticlTrackstersCLUE3DHigh_nticlTrackstersCLUE3DHighvertices',
        'ticlTrackstersCLUE3DHigh_oticlTrackstersCLUE3DHighvertices',
        # LC index + energy fraction per trackster (flat over all tracksters)
        'ticlTrackstersCLUE3DHighvertices_vertices',
        'ticlTrackstersCLUE3DHighvertices_vertex_mult',
    ],
    "ticlTracksterLinks": [
        'ticlTracksterLinks_raw_energy', 'ticlTracksterLinks_raw_em_energy',
        'ticlTracksterLinks_raw_pt', 'ticlTracksterLinks_regressed_energy',
        'ticlTracksterLinks_barycenter_x', 'ticlTracksterLinks_barycenter_y',
        'ticlTracksterLinks_barycenter_z',
        'ticlTracksterLinks_barycenter_eta', 'ticlTracksterLinks_barycenter_phi',
        'ticlTracksterLinks_time', 'ticlTracksterLinks_timeError',
        'ticlTracksterLinks_nticlTracksterLinksvertices',
        'ticlTracksterLinks_oticlTracksterLinksvertices',
        'ticlTracksterLinksvertices_vertices',
        'ticlTracksterLinksvertices_vertex_mult',
    ],
    "ticlTrackstersRecovery": [
        'ticlTrackstersRecovery_raw_energy', 'ticlTrackstersRecovery_raw_em_energy',
        'ticlTrackstersRecovery_raw_pt', 'ticlTrackstersRecovery_regressed_energy',
        'ticlTrackstersRecovery_barycenter_x', 'ticlTrackstersRecovery_barycenter_y',
        'ticlTrackstersRecovery_barycenter_z',
        'ticlTrackstersRecovery_barycenter_eta', 'ticlTrackstersRecovery_barycenter_phi',
        'ticlTrackstersRecovery_time', 'ticlTrackstersRecovery_timeError',
        'ticlTrackstersRecovery_nticlTrackstersRecoveryvertices',
        'ticlTrackstersRecovery_oticlTrackstersRecoveryvertices',
        'ticlTrackstersRecoveryvertices_vertices',
        'ticlTrackstersRecoveryvertices_vertex_mult',
    ],
}


def process_batch(ml_files):
    """Process a batch of nanoML files and return the combined data."""
    tmp_store = defaultdict(list)

    for f in tqdm(ml_files, desc="  nanoML", leave=False):
        with uproot.open(f)["Events"] as tree:
            for group_name, branches in BRANCH_GROUPS.items():
                data = tree.arrays(filter_name=branches, library="ak")
                # if len(data.fields) == 0:
                #     continue

                # Derive isPileup flag for SimCluster and MergedSimCluster:
                # signal iff bunchCrossing == 0 AND eventId == 0; anything
                # else is pileup (either OOT via BX != 0 or in-time PU
                # minbias via eventId != 0).

                if group_name in ("SimCluster", "MergedSimCluster", "MergedCaloTruthMergedSimCluster"):
                    bx_branch = f"{group_name}_bunchCrossing"
                    ev_branch = f"{group_name}_eventId"
                    is_pileup = ~((data[bx_branch] == 0) & (data[ev_branch] == 0))
                    data[f"{group_name}_isPileup"] = is_pileup

                # if group_name == "SimCluster":
                #     bx_branch = "SimCluster_bunchCrossing"
                #     ev_branch = "SimCluster_eventId"
                #     is_pileup = ~((data[bx_branch] == 0) & (data[ev_branch] == 0))
                #     data["SimCluster_isPileup"] = is_pileup

                tmp_store[group_name].append(data)

    # Concatenate arrays across files in this batch
    batch_data = {name: ak.concatenate(arr_list) for name, arr_list in tmp_store.items()}

    # Combine into single record array
    combined = ak.zip({name: arr for name, arr in batch_data.items()}, depth_limit=1)

    return combined


def main():
    args = parse_args()

    MLfileList = args.nanoMLfiles

    # Create output directory
    outdir = pathlib.Path(args.outputDir)
    outdir.mkdir(exist_ok=True)
    output_path = outdir / args.outputFile

    # Process in batches and append to a single parquet file
    batch_size = args.batch_size
    n_files = len(MLfileList)
    n_batches = (n_files + batch_size - 1) // batch_size

    print(f"Processing {n_files} nanoML files in {n_batches} batches of up to {batch_size}")
    print(f"Collections: {', '.join(BRANCH_GROUPS.keys())}")
    print(f"Derived fields: SimCluster_isPileup, MergedSimCluster_isPileup, "
          f"MergedCaloTruthMergedSimCluster_isPileup")
    print(f"Writing incrementally to {output_path}")

    parquet_writer = None

    for i in tqdm(range(n_batches), desc="Processing batches"):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, n_files)
        ml_batch = MLfileList[start_idx:end_idx]

        print(f"\nBatch {i+1}/{n_batches}: files {start_idx+1}-{end_idx}")
        batch_data = process_batch(ml_batch)

        # Convert to arrow table
        table = ak.to_arrow_table(batch_data)

        # Write or append to parquet
        if parquet_writer is None:
            parquet_writer = pq.ParquetWriter(
                output_path,
                table.schema,
                compression=args.compression,
            )

        parquet_writer.write_table(table)
        print(f"  Appended batch {i+1} to parquet")

        del batch_data, table
        gc.collect()

    if parquet_writer:
        parquet_writer.close()

    print(f"\nDone! Created single parquet file: {output_path}")


if __name__ == "__main__":
    main()
