import uproot
import numpy as np
import awkward as ak
from glob import glob
from tqdm import tqdm
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
    parser.add_argument('--step_size', type=int, default=500,
                        help='Events per chunk. Each chunk is converted and appended to the single output '
                             'parquet file as one row group, so peak memory scales with this, not with the '
                             'file size (default: 500)')
    parser.add_argument('--max_events', type=int, default=None,
                        help='Only convert the first N events of each file (for tests)')
    # kept for backwards compatibility; files are now streamed one chunk at a time
    parser.add_argument('--batch_size', type=int, default=None, help=argparse.SUPPRESS)

    return parser.parse_args()


# Branches grouped by object family. Prefixes match the nano flat table
# naming: e.g. "SimCluster_" branches all belong to the SimCluster object.
#
# Note: SimCluster_isPileup, MergedSimCluster_isPileup and
# MergedCaloTruthMergedSimCluster_isPileup are derived in process_chunk() from
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

# ---------------------------------------------------------------------------
# MC-truth tracksters and reco<->sim associations (see nanoML_cfg.py)
# ---------------------------------------------------------------------------

# ticlSimTracksters: one SimTrackster per SimCluster, or per CaloParticle when
# its G4 track crossed the calo boundary. ticlSimTrackstersFromCPs: one per
# CaloParticle. `seedIndex` is the row of the seed in the SimCluster table
# (SimCluster seed) or in the CaloPart table (CaloParticle seed); which one is
# encoded in the derived <grp>_seedIsCaloParticle flag (see
# _derive_sim_trackster_flags). The boundary*/simTime/genPt/mass
# branches describe the seed at the calo boundary.
# NB: boundaryPhi is filled with the boundary eta upstream
# (SimTracksterTableProducer bug); use boundaryPx/Py for the azimuth.
SIM_TRACKSTER_GROUPS = ("ticlSimTracksters", "ticlSimTrackstersFromCPs")


def _sim_trackster_branches(grp):
    return [
        f'{grp}_raw_energy', f'{grp}_raw_em_energy',
        f'{grp}_raw_pt', f'{grp}_regressed_energy',
        f'{grp}_barycenter_x', f'{grp}_barycenter_y', f'{grp}_barycenter_z',
        f'{grp}_barycenter_eta', f'{grp}_barycenter_phi',
        f'{grp}_time', f'{grp}_timeError', f'{grp}_boundaryTime',
        # seed (SimCluster or CaloParticle) the SimTrackster was built from
        f'{grp}_seedIndex', f'{grp}_seedProductId',
        # seed properties at the calo boundary (extension table)
        f'{grp}_boundaryX', f'{grp}_boundaryY', f'{grp}_boundaryZ',
        f'{grp}_boundaryEta', f'{grp}_boundaryPhi',
        f'{grp}_boundaryPx', f'{grp}_boundaryPy', f'{grp}_boundaryPz',
        # (simEnergy is computed upstream but not written to the table)
        f'{grp}_simTime', f'{grp}_genPt', f'{grp}_mass',
        # LayerCluster links (LC index + energy fraction, flat over tracksters)
        f'{grp}_n{grp}vertices', f'{grp}_o{grp}vertices',
        f'{grp}vertices_vertices', f'{grp}vertices_vertex_mult',
    ]


for _grp in SIM_TRACKSTER_GROUPS:
    BRANCH_GROUPS[_grp] = _sim_trackster_branches(_grp)

# Sim TICLCandidates: rows parallel to ticlSimTrackstersFromCPs;
# SimCandidate2TrackstersIndices_tracksterIndex points into ticlSimTracksters.
BRANCH_GROUPS["SimTICLCand"] = [
    'SimTICLCand_pt', 'SimTICLCand_p', 'SimTICLCand_energy', 'SimTICLCand_raw_energy',
    'SimTICLCand_eta', 'SimTICLCand_phi', 'SimTICLCand_mass',
    'SimTICLCand_pdgID', 'SimTICLCand_charge',
    'SimTICLCand_time', 'SimTICLCand_timeError',
]
BRANCH_GROUPS["SimCandidate2Tracksters"] = [
    'nSimCandidate2Tracksters',
    'nSimCandidate2TrackstersIndices',
    'SimCandidate2TrackstersIndices_tracksterIndex',
    'SimCandidate2Tracksters_nSimCandidate2TrackstersIndices',
    'SimCandidate2Tracksters_oSimCandidate2TrackstersIndices',
]

# Reco <-> Sim trackster association tables, e.g. CLUE3DHighToSimTSByHits
# (rows parallel to ticlTrackstersCLUE3DHigh, links point into
# ticlSimTracksters) and SimTSToCLUE3DHighByHits (the reverse). Each has
# per-row count/offset branches and a flat "...Links" table with the linked
# index, sharedEnergy and score (lower is better).
SIM_ASSOC_RECO = {"ticlTrackstersCLUE3DHigh": "CLUE3DHigh", "ticlTracksterLinks": "Links"}
SIM_ASSOC_SIM = {"ticlSimTracksters": "SimTS", "ticlSimTrackstersFromCPs": "SimTSCP"}
SIM_ASSOC_TYPES = ("ByHits", "ByLCs")

# table name -> (source group, target group)
SIM_ASSOC_TABLES = {}
for _reco_grp, _reco_short in SIM_ASSOC_RECO.items():
    for _sim_grp, _sim_short in SIM_ASSOC_SIM.items():
        for _type in SIM_ASSOC_TYPES:
            SIM_ASSOC_TABLES[f"{_reco_short}To{_sim_short}{_type}"] = (_reco_grp, _sim_grp)
            SIM_ASSOC_TABLES[f"{_sim_short}To{_reco_short}{_type}"] = (_sim_grp, _reco_grp)


def _assoc_branches(name):
    return [
        f'n{name}',
        f'{name}_n{name}Links', f'{name}_o{name}Links',
        f'n{name}Links',
        f'{name}Links_index', f'{name}Links_sharedEnergy', f'{name}Links_score',
    ]


for _name in SIM_ASSOC_TABLES:
    BRANCH_GROUPS[_name] = _assoc_branches(_name)


def _derive_sim_trackster_flags(per_file):
    """Add <grp>_seedIsCaloParticle to the SimTrackster groups of one file.

    All ticlSimTrackstersFromCPs entries are CaloParticle-seeded, so a
    SimTrackster is CaloParticle-seeded iff its seedProductId equals the
    fromCPs one in the same event; otherwise seedIndex points into the
    SimCluster table. No-op for files without the SimTrackster tables."""
    ref_grp = "ticlSimTrackstersFromCPs"
    ref = per_file.get(ref_grp)
    if ref is None or f"{ref_grp}_seedProductId" not in ref.fields:
        return
    # per-event ProductID of the CaloParticle collection; 0 (an invalid
    # ProductID, never matches) when the event has no CaloParticles. Filling
    # here keeps the comparison below a plain `var * bool` (no option/union
    # types, which pyarrow cannot write to parquet).
    cp_pid = ak.fill_none(ak.firsts(ref[f"{ref_grp}_seedProductId"]), 0)
    for grp in SIM_TRACKSTER_GROUPS:
        data = per_file.get(grp)
        if data is None or f"{grp}_seedProductId" not in data.fields:
            continue
        data[f"{grp}_seedIsCaloParticle"] = data[f"{grp}_seedProductId"] == cp_pid




def _chunk_to_table(chunk, schema):
    """Convert one chunk to an arrow table with the schema of the first chunk.

    Plain arrow types (no awkward extension types) so that chunks can be cast:
    a chunk in which some collection is empty everywhere may come out with a
    slightly different (e.g. null-typed) column, and every row group of a
    parquet file must share one schema."""
    table = ak.to_arrow_table(chunk, extensionarray=False)
    if schema is None:
        return table, table.schema
    if not table.schema.equals(schema):
        try:
            table = table.cast(schema)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
            raise RuntimeError(
                "Chunk schema differs from the first chunk and cannot be cast; "
                "try a larger --step_size so that the first chunk is representative.\n"
                f"{e}") from e
    return table, schema


def _iter_chunks(tree, step_size, max_events):
    """Yield (start, stop) entry ranges covering the tree."""
    n = tree.num_entries if max_events is None else min(tree.num_entries, max_events)
    for start in range(0, n, step_size):
        yield start, min(start + step_size, n)


def process_chunk(tree, start, stop):
    """Read events [start, stop) of one nanoML tree and return the combined
    record array (one entry per event)."""
    per_file = {}
    for group_name, branches in BRANCH_GROUPS.items():
        data = tree.arrays(filter_name=branches, entry_start=start, entry_stop=stop, library="ak")
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

        per_file[group_name] = data

    # SimTrackster seed type needs both SimTrackster groups of the chunk
    _derive_sim_trackster_flags(per_file)

    # Combine into single record array
    combined = ak.zip({name: arr for name, arr in per_file.items()}, depth_limit=1)

    return combined


def main():
    args = parse_args()

    MLfileList = args.nanoMLfiles

    # Create output directory
    outdir = pathlib.Path(args.outputDir)
    outdir.mkdir(exist_ok=True)
    output_path = outdir / args.outputFile

    # Stream every file in chunks of --step_size events; each chunk becomes one
    # row group of the single output parquet file, so peak memory is bounded by
    # the chunk size rather than by the file size.
    step_size = args.step_size
    n_files = len(MLfileList)

    print(f"Processing {n_files} nanoML files in chunks of {step_size} events"
          + (f" (first {args.max_events} events per file)" if args.max_events else ""))
    print(f"Collections: {', '.join(BRANCH_GROUPS.keys())}")
    print(f"Derived fields: SimCluster_isPileup, MergedSimCluster_isPileup, "
          f"MergedCaloTruthMergedSimCluster_isPileup, "
          f"ticlSimTracksters_seedIsCaloParticle, ticlSimTrackstersFromCPs_seedIsCaloParticle")
    print(f"Writing incrementally to {output_path}")

    parquet_writer = None
    schema = None
    n_written = 0

    for i, f in enumerate(MLfileList):
        with uproot.open(f)["Events"] as tree:
            chunks = list(_iter_chunks(tree, step_size, args.max_events))
            print(f"\nFile {i+1}/{n_files}: {f} ({tree.num_entries} events, {len(chunks)} chunks)")
            for start, stop in tqdm(chunks, desc="  chunks", leave=False):
                chunk = process_chunk(tree, start, stop)

                # Convert to arrow table (schema fixed by the first chunk)
                table, schema = _chunk_to_table(chunk, schema)

                # Write or append to parquet (one row group per chunk)
                if parquet_writer is None:
                    parquet_writer = pq.ParquetWriter(
                        output_path,
                        schema,
                        compression=args.compression,
                    )
                parquet_writer.write_table(table)
                n_written += len(chunk)

                del chunk, table
                gc.collect()

    if parquet_writer:
        parquet_writer.close()
    print(f"Wrote {n_written} events")

    print(f"\nDone! Created single parquet file: {output_path}")


if __name__ == "__main__":
    main()
