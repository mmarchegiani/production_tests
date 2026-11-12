import uproot
import numpy as np
import awkward as ak
from glob import glob
from tqdm import tqdm
import os, pathlib
import argparse
import gc
import shutil
import subprocess
import pyarrow.parquet as pq
import pyarrow as pa


def parse_args():
    parser = argparse.ArgumentParser(
        description='Process nanoML root files (with TICL tables inline) into a single parquet file. '
                    'For CMSSW_20_0_0_pre1: TICL is now default reco, tracksters and TICLCandidates '
                    'live in the same Events tree as SimClusters/RecHits (no separate output_ticl.root). '
                    'Each input event is split into two output events, one per HGCAL endcap; the '
                    'negative endcap is mirrored (z -> -z, eta -> -eta) so both look like +z.'
    )
    parser.add_argument('--nanoMLfiles', nargs='+', required=True, help='List of nanoML root files')
    parser.add_argument('--outputDir', type=str, default="parquet_out",
                        help='Where to write the parquet file: a local directory, or an XRootD URL '
                             '(root://host//path, e.g. root://cmsdata.phys.cmu.edu//store/group/...) '
                             'to stream it straight to remote storage. The latter needs the XRootD '
                             'python bindings (available in LCG views, not in the hgcal-offline-reco '
                             'image); without them the file is staged locally and xrdcp-ed at the end')
    parser.add_argument('--outputFile', type=str, default="output.parquet", help='Output parquet filename')
    parser.add_argument('--compression', type=str, default='lz4', help='Parquet compression algorithm')
    parser.add_argument('--step_size', type=int, default=500,
                        help='Events per chunk. Each chunk is converted and appended to the single output '
                             'parquet file as one row group, so peak memory scales with this, not with the '
                             'file size (default: 500)')
    parser.add_argument('--max_events', type=int, default=None,
                        help='Only convert the first N events of each file (for tests)')
    parser.add_argument('--stage-dir', type=str, default=os.environ.get('TMPDIR', '/tmp'),
                        help='root:// outputs without the XRootD python bindings only: local directory '
                             'holding the whole file until it is xrdcp-ed (default: $TMPDIR or /tmp)')
    parser.add_argument('--overwrite', action='store_true',
                        help='Replace an existing root:// output file (by default the script refuses '
                             'to start if the remote file already exists)')
    # kept for backwards compatibility; files are now streamed one chunk at a time
    parser.add_argument('--batch_size', type=int, default=None, help=argparse.SUPPRESS)

    return parser.parse_args()


# Branches grouped by object family. Prefixes match the nano flat table
# naming: e.g. "SimCluster_" branches all belong to the SimCluster object.
#
# Note: SimCluster_isPileup, MergedSimCluster_isPileup,
# MergedCaloTruthMergedSimCluster_isPileup, TruthCB_isPileup and TruthFG_isPileup
# are derived in process_chunk() from the corresponding bunchCrossing/eventId
# (eventIndex) branches (signal iff BX==0 AND eventId==0), TruthCB_hasHGCALHits /
# TruthFG_hasHGCALHits from <grp>_simEnergyHGCAL. They are not read from disk.
# Every group is optional: a nanoTruth_cfg.py file has no RecHit, SimCluster or
# MergedSimCluster tables, a nanoML_cfg.py file (as of Oct 2026) no TruthCB
# tables, and TruthFG needs a RECO made with truthFullGraph=1. The TICL trackster
# collections come in two versions (TICLv5: ticlTrackstersCLUE3DHigh first
# iteration; TICLv6: ticlTrackstersCLUEstering first iteration and
# ticlTracksterInterpretations final tracksters, nanoTruth_cfg.py); which one a
# file carries is checked (detect_ticl_version), a file with neither is refused.
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
        # CMSSW_20_1 (pre4_D128): the extra table extends TICLCand, so the
        # per-candidate count lives here (the standalone nCandidate2Tracksters
        # branch is gone; it equalled nTICLCand). Older names are kept for
        # pre1_D121 files; filter_name skips whatever a file does not have.
        'TICLCand_nCandidate2TrackstersIndices',
        'TICLCand_oCandidate2TrackstersIndices',
        # Per-candidate count/offset of linked tracksters. These are needed to
        # regroup the flat Indices table per candidate for the endcap split.
        # If these names don't exist in your files (check tree.keys()), the
        # code falls back to assuming exactly one trackster per candidate,
        # validated at runtime.
        'Candidate2Tracksters_nCandidate2TrackstersIndices',
        'Candidate2Tracksters_oCandidate2TrackstersIndices',
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
        'ticlTrackstersCLUE3DHigh_EV1', 'ticlTrackstersCLUE3DHigh_EV2', 'ticlTrackstersCLUE3DHigh_EV3',
        'ticlTrackstersCLUE3DHigh_eVector0_x', 'ticlTrackstersCLUE3DHigh_eVector0_y', 'ticlTrackstersCLUE3DHigh_eVector0_z',
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

# TICLv6 (RECO.py useTICL=1, nanoTruth_cfg.py): the first iteration is
# ticlTrackstersCLUEstering (in place of ticlTrackstersCLUE3DHigh) and the final
# tracksters are ticlTracksterInterpretations; both have the CLUE3DHigh columns.
TICLV6_TRACKSTER_GROUPS = ("ticlTrackstersCLUEstering", "ticlTracksterInterpretations")
for _grp in TICLV6_TRACKSTER_GROUPS:
    BRANCH_GROUPS[_grp] = [b.replace("ticlTrackstersCLUE3DHigh", _grp)
                           for b in BRANCH_GROUPS["ticlTrackstersCLUE3DHigh"]]
# Every reco trackster collection the split handles, v5 and v6 (each optional).
RECO_TRACKSTER_GROUPS = ("ticlTrackstersCLUE3DHigh", "ticlTrackstersCLUEstering",
                         "ticlTracksterLinks", "ticlTrackstersRecovery",
                         "ticlTracksterInterpretations")

# GEN particles of the gun (nanoTruth_cfg.py HGCalGenPart): kinematics and the
# production vertex, e.g. the two closeBy photons and their separation on the
# HGCal face. Assigned to an endcap by eta; no index points into or out of it.
BRANCH_GROUPS["HGCalGenPart"] = [
    'HGCalGenPart_pdgId', 'HGCalGenPart_status', 'HGCalGenPart_energy', 'HGCalGenPart_mass',
    'HGCalGenPart_pt', 'HGCalGenPart_eta', 'HGCalGenPart_phi',
    'HGCalGenPart_vx', 'HGCalGenPart_vy', 'HGCalGenPart_vz',
]

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
    # CMSSW_20_1 (pre4_D128) names, see the Candidate2Tracksters group
    'SimTICLCand_nSimCandidate2TrackstersIndices',
    'SimTICLCand_oSimCandidate2TrackstersIndices',
]

# Reco <-> Sim trackster association tables, e.g. CLUE3DHighToSimTSByHits
# (rows parallel to ticlTrackstersCLUE3DHigh, links point into
# ticlSimTracksters) and SimTSToCLUE3DHighByHits (the reverse). Each has
# per-row count/offset branches and a flat "...Links" table with the linked
# index, sharedEnergy and score (lower is better).
SIM_ASSOC_RECO = {"ticlTrackstersCLUE3DHigh": "CLUE3DHigh", "ticlTracksterLinks": "Links",
                  # TICLv6 (nanoTruth_cfg.py short names)
                  "ticlTrackstersCLUEstering": "CLUEstering",
                  "ticlTracksterInterpretations": "Interpretations"}
SIM_ASSOC_SIM = {"ticlSimTracksters": "SimTS", "ticlSimTrackstersFromCPs": "SimTSCP"}
SIM_ASSOC_TYPES = ("ByHits", "ByLCs")

# table name -> (source group, target group)
SIM_ASSOC_TABLES = {}
for _reco_grp, _reco_short in SIM_ASSOC_RECO.items():
    for _sim_grp, _sim_short in SIM_ASSOC_SIM.items():
        for _type in SIM_ASSOC_TYPES:
            SIM_ASSOC_TABLES[f"{_reco_short}To{_sim_short}{_type}"] = (_reco_grp, _sim_grp)
            SIM_ASSOC_TABLES[f"{_sim_short}To{_reco_short}{_type}"] = (_sim_grp, _reco_grp)


def _assoc_branches(name, values=("sharedEnergy", "score")):
    """Branches of a one-to-many association table: per-row count/offset, the flat
    link table with the linked index and the value columns `values` (a value a file
    lacks is skipped by filter_name)."""
    return [
        f'n{name}',
        f'{name}_n{name}Links', f'{name}_o{name}Links',
        f'n{name}Links',
        f'{name}Links_index',
    ] + [f'{name}Links_{v}' for v in values]


for _name in SIM_ASSOC_TABLES:
    BRANCH_GROUPS[_name] = _assoc_branches(_name)


# ---------------------------------------------------------------------------
# MC-truth graph: truth particle tables and their associations
# (see nanoTruth_cfg.py / nanoML_cfg.py and PhysicsTools/TruthInfo/python/truthNanoTables_cff.py)
# ---------------------------------------------------------------------------

# Two particle tables with the same columns, one per set of association maps:
#   TruthCB: one row per particle of the MC-truth graph at the tracker-calorimeter
#     boundary (truth::Level::CaloBoundary, the SimCluster analogue), signal and
#     in-time pileup (RECO.py truthAssoc=1).
#   TruthFG: one row per particle the full-graph associators may assign a reco object
#     to (RECO.py truthFullGraph=1): the boundary particles and their ancestors, so an
#     adaptive match may be an ancestor. hasBoundary is false and the boundary columns
#     are 0 for a particle that never crossed the boundary itself.
# Both hold only the particles with sim energy in HGCAL. graphId is the particle id in
# the truth graph (the key the association maps use); parentGraphId / genRootGraphId
# are graph ids too, not rows of the table. <grp>_isPileup and <grp>_hasHGCALHits are
# derived in process_chunk().
TRUTH_CB_GROUP = "TruthCB"
TRUTH_FG_GROUP = "TruthFG"
TRUTH_PARTICLE_GROUPS = (TRUTH_CB_GROUP, TRUTH_FG_GROUP)


def _truth_particle_branches(grp):
    return [
        f'{grp}_graphId', f'{grp}_pdgId', f'{grp}_charge',
        f'{grp}_isSignal', f'{grp}_bunchCrossing', f'{grp}_eventIndex',
        f'{grp}_pt', f'{grp}_eta', f'{grp}_phi', f'{grp}_energy',
        f'{grp}_hasBoundary',
        f'{grp}_boundaryX', f'{grp}_boundaryY', f'{grp}_boundaryZ', f'{grp}_boundaryT',
        f'{grp}_boundaryPx', f'{grp}_boundaryPy', f'{grp}_boundaryPz', f'{grp}_boundaryE',
        f'{grp}_boundaryPt', f'{grp}_boundaryEta', f'{grp}_boundaryPhi',
        f'{grp}_simEnergyHGCAL', f'{grp}_levelFlags',
        f'{grp}_parentGraphId', f'{grp}_genRootGraphId',
    ]


for _grp in TRUTH_PARTICLE_GROUPS:
    BRANCH_GROUPS[_grp] = _truth_particle_branches(_grp)

# Reco <-> truth association tables, same count/offset + flat Links layout as the
# SIM_ASSOC_TABLES. <reco>To<truth> rows are parallel to the reco collection and the
# link index is a <truth> row: the table without suffix is the Fixed working point
# (every particle sharing energy, best first), the AdaptiveTight / AdaptiveNominal
# tables hold the single particle of that working point (for TruthFG possibly an
# ancestor of the boundary particles). <truth>To<reco> rows are parallel to <truth>
# and the link index is a reco row. Reco->truth links carry sharedEnergy (GeV),
# truth->reco links the sim-normalised sharedEnergyFraction; both carry graphId and
# score (lower is better). Absent tables are skipped like every optional group.
TRUTH_ASSOC_RECO = {"ticlTrackstersCLUE3DHigh": "CLUE3DHigh", "LayerCluster": "LayerCluster",
                    "ticlTracksterLinks": "Links",
                    # TICLv6 (nanoTruth_cfg.py: first and final iterations only)
                    "ticlTrackstersCLUEstering": "CLUEstering",
                    "ticlTracksterInterpretations": "Interpretations"}
TRUTH_ASSOC_ADAPTIVE_POINTS = ("AdaptiveTight", "AdaptiveNominal")
TRUTH_ASSOC_VALUES = ("graphId", "sharedEnergy", "sharedEnergyFraction", "score")
# table name -> (source group, target group)
TRUTH_ASSOC_TABLES = {}
for _truth_grp in TRUTH_PARTICLE_GROUPS:
    for _reco_grp, _reco_short in TRUTH_ASSOC_RECO.items():
        TRUTH_ASSOC_TABLES[f"{_reco_short}To{_truth_grp}"] = (_reco_grp, _truth_grp)
        TRUTH_ASSOC_TABLES[f"{_truth_grp}To{_reco_short}"] = (_truth_grp, _reco_grp)
        for _wp in TRUTH_ASSOC_ADAPTIVE_POINTS:
            TRUTH_ASSOC_TABLES[f"{_reco_short}To{_truth_grp}{_wp}"] = (_reco_grp, _truth_grp)
for _name in TRUTH_ASSOC_TABLES:
    BRANCH_GROUPS[_name] = _assoc_branches(_name, TRUTH_ASSOC_VALUES)

# <group>: (bunchCrossing branch, event branch); signal iff both are 0.
PILEUP_KEYS = {
    "SimCluster": ("SimCluster_bunchCrossing", "SimCluster_eventId"),
    "MergedSimCluster": ("MergedSimCluster_bunchCrossing", "MergedSimCluster_eventId"),
    "MergedCaloTruthMergedSimCluster": ("MergedCaloTruthMergedSimCluster_bunchCrossing",
                                        "MergedCaloTruthMergedSimCluster_eventId"),
    **{_grp: (f"{_grp}_bunchCrossing", f"{_grp}_eventIndex") for _grp in TRUTH_PARTICLE_GROUPS},
}


def _derive_truth_flags(data, grp):
    """<grp>_hasHGCALHits: the object left sim energy in HGCAL (a barrel crosser or a
    particle absorbed before the calorimeter has none and is not a trainable target;
    the nano tables now write only such rows, so the flag is true everywhere)."""
    if f"{grp}_simEnergyHGCAL" in data.fields:
        data[f"{grp}_hasHGCALHits"] = data[f"{grp}_simEnergyHGCAL"] > 0


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

# ---------------------------------------------------------------------------
# Endcap split configuration
# ---------------------------------------------------------------------------

# Which trackster collection Candidate2TrackstersIndices_tracksterIndex points
# into, per TICL version of the input file: in v5 (>=15.x) the TICLCandidates
# are built from the linked tracksters, in v6 (nanoTruth_cfg.py) from the final
# ticlTracksterInterpretations. The version is read off every file
# (detect_ticl_version), never assumed.
CAND_TRACKSTER_COLLECTION = {"v5": "ticlTracksterLinks", "v6": "ticlTracksterInterpretations"}
TICL_VERSION_MARKER = {"v5": "ticlTrackstersCLUE3DHigh", "v6": "ticlTrackstersCLUEstering"}


def detect_ticl_version(branch_names):
    """'v5' or 'v6' from the branch names of a nano tree. A file carrying both
    first-iteration collections, or neither, is refused: no silent fallback."""
    found = [v for v, grp in TICL_VERSION_MARKER.items() if f"{grp}_raw_energy" in branch_names]
    if len(found) != 1:
        have = [TICL_VERSION_MARKER[v] for v in found] or "neither"
        raise RuntimeError(
            f"cannot tell the TICL version of the input: first-iteration tracksters found: {have}; "
            f"expected exactly one of {list(TICL_VERSION_MARKER.values())}")
    return found[0]

# Field used to decide endcap membership for each collection.
# Positive endcap keeps value >= 0 (so nothing exactly at 0 is lost);
# negative endcap keeps value < 0. Candidate2Tracksters rows are parallel to
# TICLCand and use its mask.
ENDCAP_KEY = {
    "RecHitHGC": "RecHitHGC_z",
    "MergedSimCluster": "MergedSimCluster_impactPoint_z",
    "MergedCaloTruthMergedSimCluster": "MergedCaloTruthMergedSimCluster_impactPoint_z",
    "SimCluster": "SimCluster_impactPoint_z",
    "LayerCluster": "LayerCluster_z",
    "TICLCand": "TICLCand_eta",
    "ticlTrackstersCLUE3DHigh": "ticlTrackstersCLUE3DHigh_barycenter_z",
    "ticlTracksterLinks": "ticlTracksterLinks_barycenter_z",
    "ticlTrackstersRecovery": "ticlTrackstersRecovery_barycenter_z",
    "ticlTrackstersCLUEstering": "ticlTrackstersCLUEstering_barycenter_z",
    "ticlTracksterInterpretations": "ticlTracksterInterpretations_barycenter_z",
    # GEN particles: by eta (the gun shoots at |eta| > 1.5, nothing sits at 0)
    "HGCalGenPart": "HGCalGenPart_eta",
    # SimTracksters: side of the seed at the calo boundary (defined even when
    # all of the SimTrackster's layer clusters were filtered away); falls back
    # to the barycenter (ENDCAP_KEY_FALLBACK) when the seed never reached the
    # boundary and boundaryZ is exactly 0.
    "ticlSimTracksters": "ticlSimTracksters_boundaryZ",
    "ticlSimTrackstersFromCPs": "ticlSimTrackstersFromCPs_boundaryZ",
    # MC-truth graph particles: side of the boundary crossing; the GEN eta decides
    # when boundaryZ is exactly 0 (no checkpoint: a TruthFG ancestor that decayed
    # before reaching the boundary, such as a pi0 or a tau).
    "TruthCB": "TruthCB_boundaryZ",
    "TruthFG": "TruthFG_boundaryZ",
}
ENDCAP_KEY_FALLBACK = {
    "ticlSimTracksters": "ticlSimTracksters_barycenter_z",
    "ticlSimTrackstersFromCPs": "ticlSimTrackstersFromCPs_barycenter_z",
    "TruthCB": "TruthCB_eta",
    "TruthFG": "TruthFG_eta",
}
# SimTICLCand (and SimCandidate2Tracksters) rows are parallel to
# ticlSimTrackstersFromCPs and reuse its mask. The association tables reuse the
# mask of their source collection (SIM_ASSOC_TABLES).

# Flip convention for the negative endcap: reflection through the z=0 plane,
# i.e. z -> -z and eta -> -eta, while x, y, phi are left untouched. Note this
# is a parity flip in z (handedness is mirrored, which is irrelevant for the
# ML inputs). detId is NOT modified and still encodes the original z-side.


# ---------------------------------------------------------------------------
# Jagged-array index bookkeeping helpers
# ---------------------------------------------------------------------------

def _exclusive_prefix_sum(jagged):
    """Per-event exclusive prefix sum of a depth-2 jagged integer array.

    Used both to build old->new index maps and to recompute the 'o...'
    offset branches after filtering.
    """
    counts = ak.to_numpy(ak.num(jagged))
    flat = ak.to_numpy(ak.flatten(jagged)).astype(np.int64)
    if flat.size == 0:
        return ak.unflatten(flat, counts)
    cs = np.cumsum(flat) - flat  # global exclusive prefix sum
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    starts = np.minimum(starts, flat.size - 1)  # guard trailing empty events
    base = np.repeat(cs[starts], counts)
    return ak.unflatten(cs - base, counts)


def _regroup(flat, counts):
    """Regroup a depth-2 flat table (events x entries) into depth-3
    (events x rows x entries) from the depth-2 per-row `counts`.

    Unlike ak.unflatten(flat, ak.flatten(counts), axis=1) this is
    unambiguous when some rows have 0 entries (e.g. a TICLCand without
    linked tracksters): axis=1 unflattening cannot tell which event a
    zero-count row belongs to and silently mis-assigns rows."""
    rows = ak.unflatten(ak.flatten(flat), ak.flatten(counts))
    return ak.unflatten(rows, ak.num(counts))


def _index_map(keep):
    """Given a jagged bool keep-mask over a collection, return a jagged int
    array of the same shape: the object's index after filtering if kept,
    else -1."""
    prefix = _exclusive_prefix_sum(ak.values_astype(keep, np.int64))
    return ak.where(keep, prefix, -1)


def _remap(idx, index_map):
    """Remap a depth-2 jagged array of indices into a collection to that
    collection's post-split indices. Entries that are negative to begin with,
    or that point at objects removed by the split (other endcap), map to -1."""
    n_ev = len(index_map)
    counts = ak.num(index_map)
    sentinel = ak.unflatten(np.full(n_ev, -1, dtype=np.int64),
                            np.ones(n_ev, dtype=np.int64))
    padded = ak.concatenate([index_map, sentinel], axis=1)
    # route original -1 entries to the per-event sentinel slot (index=counts)
    safe = ak.where(idx >= 0, idx, counts)
    return padded[safe]


def _split_matches(idx, qual, nmatch, row_counts, keep_rows, target_map):
    """Handle a (MatchIdx, MatchQual, NumMatch) triplet during the split.

    Supports both layouts, auto-detected per collection:
      * row-aligned: one (best-match) entry per source object
      * flat multi-match: NumMatch entries per source object, flattened

    target_map=None leaves index values untouched -- used for targets that are
    not stored in the parquet (CaloParticles), whose indices then still refer
    to the ORIGINAL un-split event.

    Returns (idx_out, qual_out, nmatch_out), already filtered to keep_rows.
    Fully vectorised: the flat layout is regrouped with _regroup, which is
    unambiguous for rows with zero matches."""
    remapped = _remap(idx, target_map) if target_map is not None else idx

    # Layout 1: one entry per source row
    if ak.all(ak.num(idx) == row_counts):
        q = qual[keep_rows] if qual is not None else None
        return remapped[keep_rows], q, nmatch[keep_rows]

    # Layout 2: flat multi-match list plus per-row counts
    nested_idx = _regroup(remapped, nmatch)[keep_rows]
    nested_qual = _regroup(qual, nmatch)[keep_rows] if qual is not None else None
    if target_map is not None:
        # drop matches into the removed endcap (physically shouldn't happen)
        good = nested_idx >= 0
        nested_idx = nested_idx[good]
        if nested_qual is not None:
            nested_qual = nested_qual[good]
    out_idx = ak.flatten(nested_idx, axis=2)
    out_qual = ak.flatten(nested_qual, axis=2) if nested_qual is not None else None
    out_n = ak.num(nested_idx, axis=2)
    return out_idx, out_qual, out_n


def _split_rechits(d, keep, maps, flip):
    k = keep["RecHitHGC"]
    nrow = ak.num(d["RecHitHGC_x"])
    out = {}
    for f in ('RecHitHGC_x', 'RecHitHGC_y', 'RecHitHGC_z',
              'RecHitHGC_energy', 'RecHitHGC_time', 'RecHitHGC_detId'):
        out[f] = d[f][k]

    # Best-match indices are row-aligned; remap into the split collections.
    # If a best match lands on the other endcap (essentially impossible
    # geometrically) the index becomes -1 while Qual is left as-is.
    for idxf, qualf, tgt in (
        ('RecHitHGC_MergedSimClusterBestMatchIdx',
         'RecHitHGC_MergedSimClusterBestMatchQual', 'MergedSimCluster'),
        ('RecHitHGC_SimClusterBestMatchIdx',
         'RecHitHGC_SimClusterBestMatchQual', 'SimCluster'),
        ('RecHitHGC_MergedCaloTruthMergedSimClusterBestMatchIdx',
         'RecHitHGC_MergedCaloTruthMergedSimClusterBestMatchQual', 'MergedCaloTruthMergedSimCluster'),
    ):
        if idxf not in d.fields or tgt not in maps:
            continue  # optional collection (see build_endcap) not in this file
        out[idxf] = _remap(d[idxf], maps[tgt])[k]
        out[qualf] = d[qualf][k]

    i, _, n = _split_matches(d['RecHitHGC_LayerCluster_MatchIdx'], None,
                             d['RecHitHGC_LayerClusterNumMatch'],
                             nrow, k, maps['LayerCluster'])
    out['RecHitHGC_LayerCluster_MatchIdx'] = i
    out['RecHitHGC_LayerClusterNumMatch'] = n

    if flip:
        out['RecHitHGC_z'] = -out['RecHitHGC_z']
    return out


def _split_simclusters(d, prefix, k, flip):
    # All branches are row-aligned. SimCluster_CaloPartIdx and
    # trackIdAtBoundary pass through unchanged: CaloParticles / G4 tracks are
    # not stored here, so those refer to the original un-split event.
    out = {f: d[f][k] for f in d.fields}
    if flip:
        out[f'{prefix}_impactPoint_z'] = -out[f'{prefix}_impactPoint_z']
        out[f'{prefix}_impactPoint_eta'] = -out[f'{prefix}_impactPoint_eta']
    return out


def _split_layerclusters(d, keep, maps, flip):
    k = keep["LayerCluster"]
    nrow = ak.num(d["LayerCluster_x"])
    out = {}
    for f in ('LayerCluster_x', 'LayerCluster_y', 'LayerCluster_z',
              'LayerCluster_eta', 'LayerCluster_phi', 'LayerCluster_energy',
              'LayerCluster_nHits', 'LayerCluster_seedDetId'):
        out[f] = d[f][k]

    # The legacy truth matches are optional: a nanoTruth_cfg.py file has none
    # (its layer-cluster truth is the LayerClusterToTruthCB table).
    if 'LayerCluster_SimCluster_MatchIdx' in d.fields and 'SimCluster' in maps:
        i, q, n = _split_matches(d['LayerCluster_SimCluster_MatchIdx'],
                                 d['LayerCluster_SimCluster_MatchQual'],
                                 d['LayerCluster_SimClusterNumMatch'],
                                 nrow, k, maps['SimCluster'])
        out['LayerCluster_SimCluster_MatchIdx'] = i
        out['LayerCluster_SimCluster_MatchQual'] = q
        out['LayerCluster_SimClusterNumMatch'] = n

    # CaloParticles are not stored in this file so the collection is not
    # split; indices still refer to the ORIGINAL event's CaloPart list.
    if 'LayerCluster_CaloPart_MatchIdx' in d.fields:
        i, q, n = _split_matches(d['LayerCluster_CaloPart_MatchIdx'],
                                 d['LayerCluster_CaloPart_MatchQual'],
                                 d['LayerCluster_CaloPartNumMatch'],
                                 nrow, k, None)
        out['LayerCluster_CaloPart_MatchIdx'] = i
        out['LayerCluster_CaloPart_MatchQual'] = q
        out['LayerCluster_CaloPartNumMatch'] = n

    if flip:
        out['LayerCluster_z'] = -out['LayerCluster_z']
        out['LayerCluster_eta'] = -out['LayerCluster_eta']
    return out


def _split_genparts(d, k, flip, prefix="HGCalGenPart"):
    """GEN particles: row-aligned, nothing indexes them. The z-reflection flips
    eta and the production-vertex z; vx, vy, phi, pt and energy are untouched."""
    out = {f: d[f][k] for f in d.fields}
    if flip:
        for f in (f"{prefix}_eta", f"{prefix}_vz"):
            if f in out:
                out[f] = -out[f]
    return out


def _split_ticlcands(d, k, flip, prefix="TICLCand"):
    out = {f: d[f][k] for f in d.fields}
    if flip:
        out[f'{prefix}_eta'] = -out[f'{prefix}_eta']
    return out


def _split_candidates(d, keep_cand, t_map, prefix="Candidate2Tracksters"):
    """Split a TICLCand -> trackster link table (reco: Candidate2Tracksters,
    sim: SimCandidate2Tracksters). Rows are parallel to the candidate table,
    so the candidate endcap mask is reused; trackster indices are remapped
    into the split target trackster collection (t_map)."""
    idx_b = f'{prefix}Indices_tracksterIndex'
    # Per-candidate count/offset branch names. pre1_D121 files carry them on
    # the standalone extra table (Candidate2Tracksters_n...); since CMSSW_20_1
    # (pre4_D128) the extra table extends the candidate table, so they are
    # TICLCand_n... / SimTICLCand_n... . Take whichever the file has.
    cand = {"Candidate2Tracksters": "TICLCand",
            "SimCandidate2Tracksters": "SimTICLCand"}.get(prefix, prefix)
    cnt_b = f'{prefix}_n{prefix}Indices'
    off_b = f'{prefix}_o{prefix}Indices'
    if cnt_b not in d.fields and f'{cand}_n{prefix}Indices' in d.fields:
        cnt_b = f'{cand}_n{prefix}Indices'
        off_b = f'{cand}_o{prefix}Indices'

    idx_flat = d[idx_b]
    remapped = _remap(idx_flat, t_map)
    have_counts = cnt_b in d.fields

    if have_counts:
        nested = _regroup(remapped, d[cnt_b])
    else:
        # Fallback: assume exactly one linked trackster per candidate.
        if not ak.all(ak.num(idx_flat) == ak.num(keep_cand)):
            raise RuntimeError(
                f"{prefix}: per-candidate count branch "
                f"'{cnt_b}' not found and the flat index table is not 1-to-1 "
                "with the candidates, so links cannot be regrouped per candidate. "
                "Check tree.keys() for the actual count/offset branch names "
                f"and add them to BRANCH_GROUPS['{prefix}'].")
        nested = ak.unflatten(remapped, 1, axis=1)

    nested = nested[keep_cand]
    # drop links into the removed endcap (a kept candidate's tracksters
    # should all be on its own side; this is a safety net)
    good = nested >= 0
    nested = nested[good]

    out = {}
    out[f'n{prefix}'] = ak.num(nested)
    out[idx_b] = ak.flatten(nested, axis=2)
    out[f'n{prefix}Indices'] = ak.num(out[idx_b])
    if have_counts:
        new_counts = ak.num(nested, axis=2)
        out[cnt_b] = new_counts
        if off_b in d.fields:
            out[off_b] = _exclusive_prefix_sum(new_counts)
    return out


def _split_tracksters(d, grp, keep_t, lc_map, flip):
    cnt_name = f"{grp}_n{grp}vertices"
    off_name = f"{grp}_o{grp}vertices"
    v_name = f"{grp}vertices_vertices"
    m_name = f"{grp}vertices_vertex_mult"

    # Regroup the flat vertices tables per trackster, remap LC indices into
    # the split LayerCluster collection, then filter tracksters by endcap.
    verts = _regroup(_remap(d[v_name], lc_map), d[cnt_name])[keep_t]
    mult = _regroup(d[m_name], d[cnt_name])[keep_t]

    # Safety net: drop constituent LCs assigned to the removed endcap
    # (tracksters are built per-endcap so this should never fire).
    good = verts >= 0
    verts, mult = verts[good], mult[good]

    out = {}
    for f in d.fields:
        if f in (cnt_name, off_name, v_name, m_name):
            continue
        out[f] = d[f][keep_t]
    out[cnt_name] = ak.num(verts, axis=2)
    out[off_name] = _exclusive_prefix_sum(out[cnt_name])
    out[v_name] = ak.flatten(verts, axis=2)
    out[m_name] = ak.flatten(mult, axis=2)

    if flip:
        out[f"{grp}_barycenter_z"] = -out[f"{grp}_barycenter_z"]
        out[f"{grp}_barycenter_eta"] = -out[f"{grp}_barycenter_eta"]
    return out


def _split_simtracksters(d, grp, keep_t, lc_map, sc_map, flip):
    """SimTrackster tables: same as _split_tracksters (LC links remapped,
    endcap filter, barycenter mirrored) plus the seed index and the seed's
    boundary quantities.

    seedIndex is remapped into the split SimCluster collection when the seed
    is a SimCluster (seedIsCaloParticle == False). CaloParticle seeds keep the
    ORIGINAL CaloPart index, as CaloParticles are not stored in the parquet.
    """
    out = _split_tracksters(d, grp, keep_t, lc_map, flip)
    seed_b, flag_b = f"{grp}_seedIndex", f"{grp}_seedIsCaloParticle"
    if seed_b in d.fields and flag_b in d.fields:
        seed = d[seed_b]
        if sc_map is None:
            # no SimCluster table in this file (nanoTruth_cfg.py output): the
            # index keeps referring to the ORIGINAL event's SimCluster collection
            out[seed_b] = seed[keep_t]
        else:
            out[seed_b] = ak.where(d[flag_b], seed, _remap(seed, sc_map))[keep_t]
    if flip:
        # z-reflection: position z, eta and momentum z of the seed flip sign
        # (boundaryPhi is left alone: upstream it holds the eta value anyway)
        for f in (f"{grp}_boundaryZ", f"{grp}_boundaryEta", f"{grp}_boundaryPz"):
            if f in out:
                out[f] = -out[f]
    return out


def _split_truth_particles(d, grp, k, flip):
    """TruthCB / TruthFG (MC-truth graph particles): row-aligned, no index column
    points into another parquet table. graphId, parentGraphId and genRootGraphId
    are truth-graph particle ids and pass through unchanged (the parent of a
    boundary particle is normally not a boundary particle itself; the parent of a
    TruthFG row may be another TruthFG row, but by graph id, never by row)."""
    out = {f: d[f][k] for f in d.fields}
    if flip:
        # z-reflection: position z, momentum z and eta flip sign; x, y, phi, pt,
        # energies and times are untouched. A row without boundary crossing has
        # boundary columns of exactly 0, which the flip leaves at 0.
        for f in (f"{grp}_boundaryZ", f"{grp}_boundaryEta", f"{grp}_boundaryPz",
                  f"{grp}_eta"):
            if f in out:
                out[f] = -out[f]
    return out


# Value columns of a one-to-many association link table, in output order: the
# TICL reco<->sim tables carry (sharedEnergy, score), the truth tables add
# graphId and use sharedEnergyFraction in the truth->reco direction. Values are
# never remapped, only filtered with their link.
ASSOC_VALUE_COLS = ("sharedEnergy", "sharedEnergyFraction", "score", "graphId")


def _split_assoc(d, name, keep_src, target_map):
    """Split a one-to-many association table (SIM_ASSOC_TABLES, TRUTH_ASSOC_TABLES).

    Rows are parallel to the source collection, whose endcap mask is reused;
    the linked indices are remapped into the split target collection and links
    into the removed endcap are dropped. Count/offset branches are recomputed;
    every value column present in the file follows its link."""
    cnt_b, off_b = f"{name}_n{name}Links", f"{name}_o{name}Links"
    idx_b = f"{name}Links_index"
    val_bs = [f"{name}Links_{v}" for v in ASSOC_VALUE_COLS if f"{name}Links_{v}" in d.fields]

    if not ak.all(ak.num(d[cnt_b]) == ak.num(keep_src)):
        raise RuntimeError(
            f"{name}: rows are not parallel to its source collection, so its "
            "endcap mask cannot be reused")

    idx = _regroup(_remap(d[idx_b], target_map), d[cnt_b])[keep_src]
    vals = {b: _regroup(d[b], d[cnt_b])[keep_src] for b in val_bs}

    good = idx >= 0
    idx = idx[good]
    vals = {b: v[good] for b, v in vals.items()}

    out = {}
    out[f"n{name}"] = ak.num(idx)
    out[cnt_b] = ak.num(idx, axis=2)
    out[off_b] = _exclusive_prefix_sum(out[cnt_b])
    out[idx_b] = ak.flatten(idx, axis=2)
    for b, v in vals.items():
        out[b] = ak.flatten(v, axis=2)
    out[f"n{name}Links"] = ak.num(out[idx_b])
    return out


def _present(batch, grp, field):
    """True if the (optional) collection `grp` was read with branch `field`."""
    return grp in batch and field in batch[grp].fields


def build_endcap(batch, side, cand_target):
    """Build one endcap's worth of events from the full batch.

    side=+1 keeps z (or eta) >= 0; side=-1 keeps z (or eta) < 0 and mirrors
    the geometry (z -> -z, eta -> -eta) so it looks like the +z endcap.
    All cross-collection indices are remapped to the filtered collections;
    references into removed objects become -1. cand_target is the trackster
    collection the TICLCand links point into (CAND_TRACKSTER_COLLECTION of the
    file's TICL version).
    """
    flip = side < 0

    keep = {}
    for grp, key in ENDCAP_KEY.items():
        if not _present(batch, grp, key):
            continue  # optional collection (e.g. SimTracksters) not in this file
        v = batch[grp][key]
        fallback = ENDCAP_KEY_FALLBACK.get(grp)
        if fallback is not None and fallback in batch[grp].fields:
            v = ak.where((v != 0) & ~np.isnan(v), v, batch[grp][fallback])
        keep[grp] = (v >= 0) if side > 0 else (v < 0)

    maps = {grp: _index_map(keep[grp])
            for grp in ("SimCluster", "MergedSimCluster",
                        "MergedCaloTruthMergedSimCluster", "LayerCluster",
                        *RECO_TRACKSTER_GROUPS,
                        "ticlSimTracksters", "ticlSimTrackstersFromCPs",
                        *TRUTH_PARTICLE_GROUPS)
            if grp in keep}

    out = {}
    # Every legacy group is optional. nanoML_cfg.py files carry all of them,
    # except MergedSimCluster for pileup samples (runMergedSimClusters=0) and
    # MergedCaloTruthMergedSimCluster for pre-20_1 files; nanoTruth_cfg.py files
    # carry no RecHit, SimCluster, MergedSimCluster or TICLCand tables. The
    # column order is kept so that schemas stay mergeable with older files.
    if "RecHitHGC" in keep and "LayerCluster" in maps:
        out["RecHitHGC"] = _split_rechits(batch["RecHitHGC"], keep, maps, flip)
    for grp in ("MergedSimCluster", "MergedCaloTruthMergedSimCluster", "SimCluster"):
        if grp in keep:
            out[grp] = _split_simclusters(batch[grp], grp, keep[grp], flip)
    if "LayerCluster" in keep:
        out["LayerCluster"] = _split_layerclusters(batch["LayerCluster"], keep, maps, flip)
    if "HGCalGenPart" in keep:
        out["HGCalGenPart"] = _split_genparts(batch["HGCalGenPart"], keep["HGCalGenPart"], flip)
    if "TICLCand" in keep:
        out["TICLCand"] = _split_ticlcands(batch["TICLCand"], keep["TICLCand"], flip)
        if _present(batch, "Candidate2Tracksters",
                    "Candidate2TrackstersIndices_tracksterIndex"):
            if cand_target not in maps:
                raise RuntimeError(
                    f"Candidate2Tracksters links point into {cand_target}, which this file "
                    "does not carry, so the candidate links cannot be remapped")
            out["Candidate2Tracksters"] = _split_candidates(
                batch["Candidate2Tracksters"], keep["TICLCand"], maps[cand_target])
    for grp in RECO_TRACKSTER_GROUPS:
        if grp in keep and "LayerCluster" in maps:
            out[grp] = _split_tracksters(batch[grp], grp, keep[grp],
                                         maps["LayerCluster"], flip)

    # --- MC truth: SimTracksters, sim candidates, reco<->sim associations ---
    # (all optional: skipped for files produced without the SimTrackster tables)
    for grp in SIM_TRACKSTER_GROUPS:
        if grp in keep and "LayerCluster" in maps:
            out[grp] = _split_simtracksters(batch[grp], grp, keep[grp],
                                            maps["LayerCluster"],
                                            maps.get("SimCluster"), flip)

    if ("ticlSimTrackstersFromCPs" in keep
            and _present(batch, "SimTICLCand", "SimTICLCand_eta")):
        k_cand = keep["ticlSimTrackstersFromCPs"]
        if not ak.all(ak.num(batch["SimTICLCand"]["SimTICLCand_eta"]) == ak.num(k_cand)):
            raise RuntimeError(
                "SimTICLCand is not parallel to ticlSimTrackstersFromCPs; "
                "cannot reuse its endcap mask for the sim candidates.")
        out["SimTICLCand"] = _split_ticlcands(batch["SimTICLCand"], k_cand, flip,
                                              prefix="SimTICLCand")
        if _present(batch, "SimCandidate2Tracksters",
                    "SimCandidate2TrackstersIndices_tracksterIndex"):
            out["SimCandidate2Tracksters"] = _split_candidates(
                batch["SimCandidate2Tracksters"], k_cand,
                maps["ticlSimTracksters"], prefix="SimCandidate2Tracksters")

    for name, (src_grp, tgt_grp) in SIM_ASSOC_TABLES.items():
        if (src_grp in keep and tgt_grp in maps
                and _present(batch, name, f"{name}Links_index")):
            out[name] = _split_assoc(batch[name], name, keep[src_grp], maps[tgt_grp])

    # --- MC-truth graph: truth particles and the reco <-> truth links ---
    # (optional: only files produced with the truth-graph tables carry them, and
    # TruthFG only those whose RECO ran truthFullGraph=1).
    # A link whose target landed in the other endcap is dropped by _split_assoc;
    # for HGCAL objects that is ~never, barrel crossers excepted.
    for grp in TRUTH_PARTICLE_GROUPS:
        if grp in keep:
            out[grp] = _split_truth_particles(batch[grp], grp, keep[grp], flip)
    for name, (src_grp, tgt_grp) in TRUTH_ASSOC_TABLES.items():
        if (src_grp in keep and tgt_grp in maps
                and _present(batch, name, f"{name}Links_index")):
            out[name] = _split_assoc(batch[name], name, keep[src_grp], maps[tgt_grp])

    return ak.zip({name: ak.zip(flds, depth_limit=1) for name, flds in out.items()},
                  depth_limit=1)




# ---------------------------------------------------------------------------
# Output destination: local directory or XRootD URL (root://host//path)
# ---------------------------------------------------------------------------
#
# With --outputDir root://host//path the parquet file is streamed to the
# remote storage one row group at a time, so the (multi-GB) output never needs
# local disk space. The remote file is written as <name>.part and renamed on
# success, so a file carrying the final name is always complete; on any error
# the partial file is removed. Requires the XRootD python bindings (LCG views
# have them; the hgcal-offline-reco apptainer image does not): without them
# the file is staged in --stage-dir and copied with xrdcp at the end.

XROOTD_PREFIX = "root://"


def is_xrootd_url(path):
    return str(path).startswith(XROOTD_PREFIX)


def _split_xrootd_url(url):
    """'root://host[:port]//path' -> ('root://host[:port]', '/path')."""
    host, sep, path = url[len(XROOTD_PREFIX):].partition("/")
    if not host or not sep or not path.strip("/"):
        raise ValueError(f"malformed XRootD URL {url!r}: expected root://host//path")
    return XROOTD_PREFIX + host, "/" + path.lstrip("/")


class _XRootDOutputFile:
    """Write-only file object over XRootD.client.File with the interface
    pyarrow.parquet.ParquetWriter needs (write/tell/flush/close).

    pyarrow writes a parquet row group as hundreds of small pieces (one per
    column page); each XRootD write is a synchronous round trip, which over a
    transatlantic link costs ~2 min per row group. Writes are therefore
    buffered and sent in CHUNK-sized requests."""

    CHUNK = 32 << 20

    def __init__(self, url):
        from XRootD import client
        from XRootD.client.flags import AccessMode, OpenFlags
        self.url = url
        self._pos = 0          # bytes already sent
        self._buf = bytearray()
        self.closed = False
        self._f = client.File()
        # mode 0644 like xrdcp (the bindings' default leaves the file 0600,
        # unreadable by the other users of a group area)
        mode = AccessMode.UR | AccessMode.UW | AccessMode.GR | AccessMode.OR
        status, _ = self._f.open(url, OpenFlags.DELETE | OpenFlags.MAKEPATH, mode)
        if not status.ok:
            raise OSError(f"cannot open {url} for writing: {status.message}")

    def write(self, data):
        view = memoryview(data).cast("B")
        self._buf += view
        if len(self._buf) >= self.CHUNK:
            self._send()
        return len(view)

    def _send(self):
        buf, self._buf = memoryview(self._buf), bytearray()
        for start in range(0, len(buf), self.CHUNK):
            piece = buf[start:start + self.CHUNK].tobytes()
            status, _ = self._f.write(piece, offset=self._pos)
            if not status.ok:
                raise OSError(f"XRootD write to {self.url} failed at offset "
                              f"{self._pos}: {status.message}")
            self._pos += len(piece)

    def tell(self):
        return self._pos + len(self._buf)

    def flush(self):
        self._send()

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return False

    def close(self):
        # ParquetWriter.close() closes its sink itself; stay idempotent so the
        # output target can close it again.
        if self.closed:
            return
        self._send()
        self.closed = True
        status, _ = self._f.close()
        if not status.ok:
            raise OSError(f"XRootD close of {self.url} failed: {status.message}")


class LocalOutput:
    """Plain local file, written in place (unchanged behaviour)."""

    def __init__(self, outdir, outfile):
        outdir = pathlib.Path(outdir)
        outdir.mkdir(exist_ok=True)
        self.final = str(outdir / outfile)

    def describe(self):
        return f"local file {self.final}"

    def sink(self):
        return self.final

    def finalize(self):
        pass

    def abort(self):
        pass


class XRootDStreamOutput:
    """Stream the parquet file to root://host//path through the XRootD python
    bindings, as <path>.part, renamed to <path> once complete."""

    def __init__(self, url, overwrite):
        from XRootD import client
        self.host, self.path = _split_xrootd_url(url)
        self.part = self.path + ".part"
        self.final = f"{self.host}/{self.path}"
        self.overwrite = overwrite
        self._fs = client.FileSystem(self.host)
        status, _ = self._fs.stat(self.path)
        if status.ok and not overwrite:
            raise FileExistsError(
                f"{self.final} already exists; remove it or pass --overwrite")
        # Open the remote file now, so that path/permission problems show up
        # at startup rather than after the first chunk has been processed.
        self._file = _XRootDOutputFile(f"{self.host}/{self.part}")

    def describe(self):
        return (f"XRootD stream to {self.final} (written as {self.part}, "
                "renamed when complete)")

    def sink(self):
        return self._file

    def finalize(self):
        written = self._file.tell()
        self._file.close()
        if written == 0:  # no chunk was written: leave no empty file behind
            self._fs.rm(self.part)
            return
        if self.overwrite:
            self._fs.rm(self.path)  # may not exist; mv below reports real errors
        status, _ = self._fs.mv(self.part, self.path)
        if not status.ok:
            raise OSError(
                f"cannot rename {self.part} to {self.path} on {self.host}: "
                f"{status.message}. The complete file is at {self.host}/{self.part}")

    def abort(self):
        try:
            self._file.close()
        except OSError:
            pass
        status, _ = self._fs.rm(self.part)
        print(f"Removed partial output {self.host}/{self.part}" if status.ok
              else f"WARNING: could not remove partial output {self.host}/{self.part}: {status.message}")


class XRootDStagedOutput:
    """Fallback without the XRootD python bindings: write to a local staging
    file and xrdcp it to root://host//path at the end (needs disk space for
    the whole file in --stage-dir)."""

    def __init__(self, url, outfile, stage_dir, overwrite):
        self.final = url
        self.overwrite = overwrite
        stage_dir = pathlib.Path(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        self.local = stage_dir / outfile
        host, path = _split_xrootd_url(url)
        if not overwrite and shutil.which("xrdfs") is not None:
            exists = subprocess.run(["xrdfs", host, "stat", path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if exists.returncode == 0:
                raise FileExistsError(f"{url} already exists; remove it or pass --overwrite")

    def describe(self):
        return f"local staging file {self.local}, xrdcp-ed to {self.final} at the end"

    def sink(self):
        return str(self.local)

    def finalize(self):
        if not self.local.exists():
            return
        cmd = ["xrdcp", "-p"] + (["-f"] if self.overwrite else []) + [str(self.local), self.final]
        print("Copying to remote storage:", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"xrdcp failed (exit {e.returncode}); the complete file is still at {self.local}") from e
        self.local.unlink()

    def abort(self):
        if self.local.exists():
            self.local.unlink()
            print(f"Removed partial staging file {self.local}")


def make_output(outdir, outfile, stage_dir, overwrite):
    """Pick the output backend from --outputDir."""
    if not is_xrootd_url(outdir):
        return LocalOutput(outdir, outfile)
    url = outdir.rstrip("/") + "/" + outfile
    try:
        import XRootD.client  # noqa: F401
    except ImportError:
        if shutil.which("xrdcp") is None:
            raise RuntimeError(
                f"writing to {url} needs the XRootD python bindings (e.g. an LCG view: "
                "source /cvmfs/sft.cern.ch/lcg/views/LCG_109/x86_64-el9-gcc13-opt/setup.sh) "
                "or at least xrdcp in PATH for the staged fallback; this python has neither")
        return XRootDStagedOutput(url, outfile, stage_dir, overwrite)
    return XRootDStreamOutput(url, overwrite)


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


def process_chunk(tree, start, stop, cand_target):
    """Read events [start, stop) of one nanoML tree, split every event into
    its two endcaps, and return the combined data (2 output events per input
    event, interleaved: [+z of ev0, -z of ev0, +z of ev1, ...])."""
    per_file = {}
    for group_name, branches in BRANCH_GROUPS.items():
        data = tree.arrays(filter_name=branches, entry_start=start, entry_stop=stop, library="ak")
        if len(data.fields) == 0:
            # optional collection not in this file: MergedSimCluster for pileup
            # samples (nanoML runMergedSimClusters=0, pepr's SimClusterMerger is
            # not pileup-aware), the SimTrackster tables for older files
            continue

        # Derive isPileup flag for SimCluster and MergedSimCluster:
        # signal iff bunchCrossing == 0 AND eventId == 0; anything
        # else is pileup (either OOT via BX != 0 or in-time PU
        # minbias via eventId != 0).
        if group_name in PILEUP_KEYS:
            bx_branch, ev_branch = PILEUP_KEYS[group_name]
            is_pileup = ~((data[bx_branch] == 0) & (data[ev_branch] == 0))
            data[f"{group_name}_isPileup"] = is_pileup
        if group_name in TRUTH_PARTICLE_GROUPS:
            _derive_truth_flags(data, group_name)

        per_file[group_name] = data

    # SimTrackster seed type needs both SimTrackster groups of the chunk
    _derive_sim_trackster_flags(per_file)
    batch = per_file

    # Split each event into two: +z endcap as-is, -z endcap mirrored to +z
    pos = build_endcap(batch, +1, cand_target)
    neg = build_endcap(batch, -1, cand_target)

    both = ak.concatenate([pos, neg])
    n = len(pos)
    order = np.empty(2 * n, dtype=np.int64)
    order[0::2] = np.arange(n)       # +z half of input event i -> row 2i
    order[1::2] = n + np.arange(n)   # mirrored -z half        -> row 2i+1
    return both[order]


def main():
    args = parse_args()

    MLfileList = args.nanoMLfiles

    # Local directory or root://host//path (see make_output); fails early if
    # the remote file exists or the python lacks XRootD support.
    output = make_output(args.outputDir, args.outputFile, args.stage_dir, args.overwrite)

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
          f"ticlSimTracksters_seedIsCaloParticle, ticlSimTrackstersFromCPs_seedIsCaloParticle, "
          f"TruthCB_isPileup, TruthCB_hasHGCALHits, TruthFG_isPileup, TruthFG_hasHGCALHits")
    print(f"Endcap split: 2 output events per input event "
          f"(-z endcap mirrored via z->-z, eta->-eta; TruthCB/TruthFG by the sign of boundaryZ, "
          f"of the GEN eta without a boundary crossing)")
    print(f"Candidate links point into {CAND_TRACKSTER_COLLECTION} by TICL version, "
          f"read off every file ({TICL_VERSION_MARKER})")
    print(f"Writing incrementally to {output.describe()}")

    parquet_writer = None
    schema = None
    n_written = 0
    ticl_version = None

    try:
        for i, f in enumerate(MLfileList):
            with uproot.open(f)["Events"] as tree:
                version = detect_ticl_version(set(tree.keys()))
                if ticl_version is None:
                    ticl_version = version
                elif version != ticl_version:
                    raise RuntimeError(f"{f} is a TICL {version} file but the first input was "
                                       f"TICL {ticl_version}; one parquet file holds one version")
                cand_target = CAND_TRACKSTER_COLLECTION[version]
                chunks = list(_iter_chunks(tree, step_size, args.max_events))
                print(f"\nFile {i+1}/{n_files}: {f} ({tree.num_entries} events, {len(chunks)} chunks, "
                      f"TICL {version}, candidate links into {cand_target})")
                for start, stop in tqdm(chunks, desc="  chunks", leave=False):
                    chunk = process_chunk(tree, start, stop, cand_target)

                    # Convert to arrow table (schema fixed by the first chunk)
                    table, schema = _chunk_to_table(chunk, schema)

                    # Write or append to parquet (one row group per chunk)
                    if parquet_writer is None:
                        parquet_writer = pq.ParquetWriter(
                            output.sink(),
                            schema,
                            compression=args.compression,
                        )
                    parquet_writer.write_table(table)
                    n_written += len(chunk)

                    del chunk, table
                    gc.collect()

        if parquet_writer:
            parquet_writer.close()
        output.finalize()
    except BaseException:
        # Leave no half-written file behind on remote storage (local outputs
        # are left as they are). Close the writer first so pyarrow does not
        # try to flush into an already-removed file afterwards.
        if parquet_writer is not None:
            try:
                parquet_writer.close()
            except Exception:
                pass
            parquet_writer = None
        output.abort()
        raise
    print(f"Wrote {n_written} split events ({n_written // 2} input events)")

    print(f"\nDone! Created single parquet file: {output.final}")


if __name__ == "__main__":
    main()
