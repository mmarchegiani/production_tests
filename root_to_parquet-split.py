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
                    'live in the same Events tree as SimClusters/RecHits (no separate output_ticl.root). '
                    'Each input event is split into two output events, one per HGCAL endcap; the '
                    'negative endcap is mirrored (z -> -z, eta -> -eta) so both look like +z.'
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
# Endcap split configuration
# ---------------------------------------------------------------------------

# Which trackster collection Candidate2TrackstersIndices_tracksterIndex points
# into. In >=15.x TICLCandidates are built from the linked tracksters; verify
# this matches your production before trusting the remapped candidate links.
CAND_TRACKSTER_COLLECTION = "ticlTracksterLinks"

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
}

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


# def _split_matches(idx, qual, nmatch, row_counts, keep_rows, target_map):
#     """Handle a (MatchIdx, MatchQual, NumMatch) triplet during the split.

#     Supports both layouts, auto-detected per collection:
#       * row-aligned: one (best-match) entry per source object
#       * flat multi-match: NumMatch entries per source object, flattened

#     target_map=None leaves index values untouched -- used for targets that are
#     not stored in the parquet (CaloParticles), whose indices then still refer
#     to the ORIGINAL un-split event.

#     Returns (idx_out, qual_out, nmatch_out), already filtered to keep_rows.
#     """
#     remapped = _remap(idx, target_map) if target_map is not None else idx
#     if ak.all(ak.num(idx) == row_counts):
#         # single match per row
#         q = qual[keep_rows] if qual is not None else None
#         return remapped[keep_rows], q, nmatch[keep_rows]

#     # flat multi-match layout: regroup per source object via NumMatch
#     fc = ak.flatten(nmatch)
#     nested_idx = ak.unflatten(remapped, fc, axis=1)[keep_rows]
#     nested_qual = (ak.unflatten(qual, fc, axis=1)[keep_rows]
#                    if qual is not None else None)
#     if target_map is not None:
#         # drop matches into the removed endcap (physically shouldn't happen)
#         good = nested_idx >= 0
#         nested_idx = nested_idx[good]
#         if nested_qual is not None:
#             nested_qual = nested_qual[good]
#     out_idx = ak.flatten(nested_idx, axis=2)
#     out_qual = ak.flatten(nested_qual, axis=2) if nested_qual is not None else None
#     out_n = ak.num(nested_idx, axis=2)
#     return out_idx, out_qual, out_n

def _split_matches(idx, qual, nmatch, row_counts, keep_rows, target_map):
    remapped = _remap(idx, target_map) if target_map is not None else idx

    # Layout 1: one entry per source row
    if ak.all(ak.num(idx) == row_counts):
        idx_events = ak.to_list(remapped)
        keep_events = ak.to_list(keep_rows)
        nmatch_events = ak.to_list(nmatch)
        qual_events = ak.to_list(qual) if qual is not None else None

        out_idx = []
        out_qual = [] if qual_events is not None else None
        out_n = []

        for ev_i, ev_keep, ev_n in zip(idx_events, keep_events, nmatch_events):
            out_idx.append([value for value, keep in zip(ev_i, ev_keep) if keep])
            out_n.append([value for value, keep in zip(ev_n, ev_keep) if keep])

        if qual_events is not None:
            for ev_q, ev_keep in zip(qual_events, keep_events):
                out_qual.append([value for value, keep in zip(ev_q, ev_keep) if keep])

        return (
            ak.Array(out_idx),
            ak.Array(out_qual) if out_qual is not None else None,
            ak.Array(out_n),
        )

    # Layout 2: flattened multi-match list plus per-row counts
    idx_events = ak.to_list(remapped)
    keep_events = ak.to_list(keep_rows)
    nmatch_events = ak.to_list(nmatch)
    qual_events = ak.to_list(qual) if qual is not None else None

    out_idx = []
    out_qual = [] if qual_events is not None else None
    out_n = []

    for iev, (ev_idx, ev_keep, ev_nmatch) in enumerate(zip(idx_events, keep_events, nmatch_events)):
        ev_out_idx = []
        ev_out_n = []
        ev_out_qual = [] if qual_events is not None else None

        ev_qual = qual_events[iev] if qual_events is not None else None
        pos = 0

        for keep, count in zip(ev_keep, ev_nmatch):
            chunk_idx = ev_idx[pos:pos + count]
            chunk_qual = ev_qual[pos:pos + count] if ev_qual is not None else None
            pos += count

            if not keep:
                continue

            if target_map is not None:
                good = [value >= 0 for value in chunk_idx]
                chunk_idx = [value for value, is_good in zip(chunk_idx, good) if is_good]
                if chunk_qual is not None:
                    chunk_qual = [value for value, is_good in zip(chunk_qual, good) if is_good]

            ev_out_idx.extend(chunk_idx)
            ev_out_n.append(len(chunk_idx))

            if ev_out_qual is not None:
                ev_out_qual.extend(chunk_qual)

        out_idx.append(ev_out_idx)
        out_n.append(ev_out_n)
        if out_qual is not None:
            out_qual.append(ev_out_qual)

    return (
        ak.Array(out_idx),
        ak.Array(out_qual) if out_qual is not None else None,
        ak.Array(out_n),
    )


# ---------------------------------------------------------------------------
# Per-group split processors
# ---------------------------------------------------------------------------

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

    i, q, n = _split_matches(d['LayerCluster_SimCluster_MatchIdx'],
                             d['LayerCluster_SimCluster_MatchQual'],
                             d['LayerCluster_SimClusterNumMatch'],
                             nrow, k, maps['SimCluster'])
    out['LayerCluster_SimCluster_MatchIdx'] = i
    out['LayerCluster_SimCluster_MatchQual'] = q
    out['LayerCluster_SimClusterNumMatch'] = n

    # CaloParticles are not stored in this file so the collection is not
    # split; indices still refer to the ORIGINAL event's CaloPart list.
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


def _split_ticlcands(d, k, flip):
    out = {f: d[f][k] for f in d.fields}
    if flip:
        out['TICLCand_eta'] = -out['TICLCand_eta']
    return out


def _split_candidates(d, keep_cand, t_map):
    """Split the TICLCand -> trackster link table. Rows are parallel to
    TICLCand, so the TICLCand endcap mask is reused; trackster indices are
    remapped into the split CAND_TRACKSTER_COLLECTION."""
    idx_flat = d['Candidate2TrackstersIndices_tracksterIndex']
    remapped = _remap(idx_flat, t_map)

    cnt_b = 'Candidate2Tracksters_nCandidate2TrackstersIndices'
    off_b = 'Candidate2Tracksters_oCandidate2TrackstersIndices'
    have_counts = cnt_b in d.fields

    if have_counts:
        nested = ak.unflatten(remapped, ak.flatten(d[cnt_b]), axis=1)
    else:
        # Fallback: assume exactly one linked trackster per candidate.
        if not ak.all(ak.num(idx_flat) == ak.num(keep_cand)):
            raise RuntimeError(
                "Candidate2Tracksters: per-candidate count branch "
                f"'{cnt_b}' not found and the flat index table is not 1-to-1 "
                "with TICLCand, so links cannot be regrouped per candidate. "
                "Check tree.keys() for the actual count/offset branch names "
                "and add them to BRANCH_GROUPS['Candidate2Tracksters'].")
        nested = ak.unflatten(remapped, 1, axis=1)

    nested = nested[keep_cand]
    # drop links into the removed endcap (a kept candidate's tracksters
    # should all be on its own side; this is a safety net)
    good = nested >= 0
    nested = nested[good]

    out = {}
    out['nCandidate2Tracksters'] = ak.num(nested)
    out['Candidate2TrackstersIndices_tracksterIndex'] = ak.flatten(nested, axis=2)
    out['nCandidate2TrackstersIndices'] = ak.num(
        out['Candidate2TrackstersIndices_tracksterIndex'])
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
    fc = ak.flatten(d[cnt_name])
    verts = ak.unflatten(_remap(d[v_name], lc_map), fc, axis=1)[keep_t]
    mult = ak.unflatten(d[m_name], fc, axis=1)[keep_t]

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


def build_endcap(batch, side):
    """Build one endcap's worth of events from the full batch.

    side=+1 keeps z (or eta) >= 0; side=-1 keeps z (or eta) < 0 and mirrors
    the geometry (z -> -z, eta -> -eta) so it looks like the +z endcap.
    All cross-collection indices are remapped to the filtered collections;
    references into removed objects become -1.
    """
    flip = side < 0

    keep = {}
    for grp, key in ENDCAP_KEY.items():
        v = batch[grp][key]
        keep[grp] = (v >= 0) if side > 0 else (v < 0)

    maps = {grp: _index_map(keep[grp])
            for grp in ("SimCluster", "MergedSimCluster",
                        "MergedCaloTruthMergedSimCluster", "LayerCluster",
                        CAND_TRACKSTER_COLLECTION)}

    out = {
        "RecHitHGC": _split_rechits(batch["RecHitHGC"], keep, maps, flip),
        "MergedSimCluster": _split_simclusters(
            batch["MergedSimCluster"], "MergedSimCluster",
            keep["MergedSimCluster"], flip),
        "MergedCaloTruthMergedSimCluster": _split_simclusters(
            batch["MergedCaloTruthMergedSimCluster"], "MergedCaloTruthMergedSimCluster",
            keep["MergedCaloTruthMergedSimCluster"], flip),
        "SimCluster": _split_simclusters(
            batch["SimCluster"], "SimCluster", keep["SimCluster"], flip),
        "LayerCluster": _split_layerclusters(batch["LayerCluster"], keep, maps, flip),
        "TICLCand": _split_ticlcands(batch["TICLCand"], keep["TICLCand"], flip),
        "Candidate2Tracksters": _split_candidates(
            batch["Candidate2Tracksters"], keep["TICLCand"],
            maps[CAND_TRACKSTER_COLLECTION]),
    }
    for grp in ("ticlTrackstersCLUE3DHigh", "ticlTracksterLinks",
                "ticlTrackstersRecovery"):
        out[grp] = _split_tracksters(batch[grp], grp, keep[grp],
                                     maps["LayerCluster"], flip)

    return ak.zip({name: ak.zip(flds, depth_limit=1) for name, flds in out.items()},
                  depth_limit=1)


def process_batch(ml_files):
    """Process a batch of nanoML files, split every event into its two
    endcaps, and return the combined data (2 output events per input event,
    interleaved: [+z of ev0, -z of ev0, +z of ev1, ...])."""
    tmp_store = defaultdict(list)

    for f in tqdm(ml_files, desc="  nanoML", leave=False):
        with uproot.open(f)["Events"] as tree:
            for group_name, branches in BRANCH_GROUPS.items():
                data = tree.arrays(filter_name=branches, library="ak")

                # Derive isPileup flag for SimCluster and MergedSimCluster:
                # signal iff bunchCrossing == 0 AND eventId == 0; anything
                # else is pileup (either OOT via BX != 0 or in-time PU
                # minbias via eventId != 0).
                if group_name in ("SimCluster", "MergedSimCluster", "MergedCaloTruthMergedSimCluster"):
                    bx_branch = f"{group_name}_bunchCrossing"
                    ev_branch = f"{group_name}_eventId"
                    is_pileup = ~((data[bx_branch] == 0) & (data[ev_branch] == 0))
                    data[f"{group_name}_isPileup"] = is_pileup

                tmp_store[group_name].append(data)

    # Concatenate arrays across files in this batch
    batch = {name: ak.concatenate(arr_list) for name, arr_list in tmp_store.items()}

    # Split each event into two: +z endcap as-is, -z endcap mirrored to +z
    pos = build_endcap(batch, +1)
    neg = build_endcap(batch, -1)

    both = ak.concatenate([pos, neg])
    n = len(pos)
    order = np.empty(2 * n, dtype=np.int64)
    order[0::2] = np.arange(n)       # +z half of input event i -> row 2i
    order[1::2] = n + np.arange(n)   # mirrored -z half        -> row 2i+1
    return both[order]


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
    print(f"Endcap split: 2 output events per input event "
          f"(-z endcap mirrored via z->-z, eta->-eta)")
    print(f"Candidate links assumed to point into: {CAND_TRACKSTER_COLLECTION}")
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
        print(f"  Appended batch {i+1} to parquet ({len(batch_data)} split events)")

        del batch_data, table
        gc.collect()

    if parquet_writer:
        parquet_writer.close()

    print(f"\nDone! Created single parquet file: {output_path}")


if __name__ == "__main__":
    main()
