# coding: utf-8

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing

# option parsing
options = VarParsing('python')
options.setDefault('outputFile', 'file:partGun_PDGid22_x96_Pt1.0To100.0_RECO_1.root')
options.setDefault('inputFiles', "file://partGun_PDGid22_x96_Pt1.0To100.0_GSD_1.root")
options.setDefault('maxEvents', -1)
options.register('outputFileDQM', 'file:partGun_PDGid22_x96_Pt1.0To100.0_DQM_1.root',
    VarParsing.multiplicity.singleton, VarParsing.varType.string, 'path to the DQM output file')
options.register("nThreads", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "number of threads")
options.register("useTICL", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "use TICL v6 (1=True, 0=False): the ticl_v6 process modifier, whose first iteration is "
    "CLUEstering (ticlTrackstersCLUEstering replaces ticlTrackstersCLUE3DHigh) and whose final "
    "tracksters are ticlTracksterInterpretations (ticlCandidate then holds the candidates only)")
options.register("truthAssoc", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "associate ticlTrackstersCLUE3DHigh and hgcalMergeLayerClusters to the calo-boundary "
    "particles of the MC-truth graph (1=True, 0=False)")
options.register("truthFullGraph", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "also run the stock full-graph adaptive trackster associators (every TICL trackster "
    "collection, every truth level, adaptive climb) for merged-topology studies (1=True, 0=False)")
options.parseArguments()

# Import process based on useTICL flag
if options.useTICL:
    from reco_prodtools.templates.RECO_ticl_v6_fragment import process  
else:
    from reco_prodtools.templates.RECO_fragment import process

# The TICL trackster collections of this reconstruction, as the fragment's process modifiers
# set them (v5: CLUE3DHigh, Links, Candidate, SuperclusteringDNN; v6: CLUEstering, Links,
# Interpretations, SuperclusteringDNN). The stock sim associators are built from the same
# list; the truth-graph associators below are keyed on it too.
from RecoTICL.Configuration.iterativeTICL_cff import ticlIterLabelsPSet
_ticlIterLabels = list(ticlIterLabelsPSet.labels)
# The first (pattern-recognition) iteration: ticlTrackstersCLUE3DHigh or ticlTrackstersCLUEstering.
_ticlFirstIteration = next(l for l in _ticlIterLabels if l.startswith("ticlTracksters"))
# The final tracksters: ticlTracksterInterpretations in v6. In v5 they are the ticlCandidate
# ones, which the calo-boundary truth has never covered.
_ticlFinalTracksters = ("ticlTracksterInterpretations"
                        if "ticlTracksterInterpretations" in _ticlIterLabels else None)

process.maxEvents.input = cms.untracked.int32(options.maxEvents)

process.load("SimTracker.TrackAssociation.trackingParticleRecoTrackAsssociation_cfi")
# append the HGCTruthProducer to the recosim step

# If you want to run the associations or SC merging
from SimCalorimetry.HGCalSimProducers.hgcHitAssociation_cfi import lcAssocByEnergyScoreProducer, scAssocByEnergyScoreProducer
from SimCalorimetry.HGCalAssociatorProducers.LCToCPAssociation_cfi import layerClusterCaloParticleAssociation as layerClusterCaloParticleAssociationProducer
from SimCalorimetry.HGCalAssociatorProducers.LCToSCAssociation_cfi import layerClusterSimClusterAssociation as layerClusterSimClusterAssociationProducer
from SimGeneral.TrackingAnalysis.simHitTPAssociation_cfi import simHitTPAssocProducer

process.lcAssocByEnergyScoreProducer = lcAssocByEnergyScoreProducer
# NB: the module labels must match the producer type: ticlSimTracksters consumes
# layerClusterSimClusterAssociationProducer as an LC->SimCluster map and
# layerClusterCaloParticleAssociationProducer as an LC->CaloParticle map.
process.layerClusterCaloParticleAssociationProducer = layerClusterCaloParticleAssociationProducer
process.scAssocByEnergyScoreProducer = scAssocByEnergyScoreProducer
process.layerClusterSimClusterAssociationProducer = layerClusterSimClusterAssociationProducer
process.simHitTPAssocProducer = simHitTPAssocProducer

process.hgcalAssociators = cms.Task(
    process.lcAssocByEnergyScoreProducer,
    process.layerClusterCaloParticleAssociationProducer,
    process.scAssocByEnergyScoreProducer,
    process.layerClusterSimClusterAssociationProducer,
    process.trackingParticleRecoTrackAsssociation,
    process.simHitTPAssocProducer,
    # Inputs of trackingParticleRecoTrackAsssociation. They are defined by the
    # RECO fragment but, since CMSSW_20_1, no longer run by the RECOSIM step, so
    # they have to be scheduled here.
    process.tpClusterProducer,
    process.quickTrackAssociatorByHits,
)

process.assoc = cms.Sequence(process.hgcalAssociators)

process.recosim_step *= process.assoc

# ---------------------------------------------------------------------------
# SimTracksters (MC-truth tracksters) and Trackster <-> SimTrackster associators.
# In CMSSW these live in the validation sequence (globalPrevalidationHGCal), not
# in RECO, so they have to be scheduled explicitly here.
#
# ticlSimTracksters produces (all under the module label "ticlSimTracksters"):
#   - TracksterCollection ""        : one SimTrackster per SimCluster (or per
#                                     CaloParticle when its G4 track crossed the
#                                     calo boundary); seedID()/seedIndex() point
#                                     back to the seeding SimCluster/CaloParticle
#   - TracksterCollection "fromCPs" : one SimTrackster per CaloParticle
#   - TracksterCollection "PU"      : a single trackster with all PU layer clusters
#   - std::vector<TICLCandidate>    : sim TICLCandidates (SimTrackster + reco track)
#   - std::map<uint,vector<uint>>   : CaloParticle index -> SimTrackster indices
#   - std::vector<float> "" / "fromCPs": layer-cluster masks
# ---------------------------------------------------------------------------
from SimCalorimetry.TICLSimProducers.SimTracksters_cff import filteredLayerClustersSimTracksters, ticlSimTracksters
from SimCalorimetry.HGCalAssociatorProducers.LCToTSAssociator_cfi import allLayerClusterToTracksterAssociations
from SimCalorimetry.HGCalAssociatorProducers.HitToTracksterAssociation_cfi import allHitToTracksterAssociations
from SimCalorimetry.HGCalAssociatorProducers.hitToSimClusterCaloParticleAssociator_cfi import hitToSimClusterCaloParticleAssociator
from SimCalorimetry.HGCalAssociatorProducers.TSToSimTSAssociation_cfi import allTrackstersToSimTrackstersAssociationsByLCs
from SimCalorimetry.HGCalAssociatorProducers.TSToSimTSAssociationByHits_cfi import allTrackstersToSimTrackstersAssociationsByHits
from SimCalorimetry.HGCalAssociatorProducers.SimClusterToCaloParticleAssociation_cfi import SimClusterToCaloParticleAssociation

process.filteredLayerClustersSimTracksters = filteredLayerClustersSimTracksters
process.ticlSimTracksters = ticlSimTracksters
# LC->Trackster and RecHit->Trackster maps for every TICL iteration plus the
# SimTracksters; inputs of the ByLCs / ByHits Trackster->SimTrackster associators.
process.allLayerClusterToTracksterAssociations = allLayerClusterToTracksterAssociations
process.allHitToTracksterAssociations = allHitToTracksterAssociations
process.hitToSimClusterCaloParticleAssociator = hitToSimClusterCaloParticleAssociator
# Reco Trackster <-> SimTrackster associations (both directions, instance labels
# e.g. "ticlTrackstersCLUE3DHighToticlSimTracksters", "ticlSimTrackstersToticlTrackstersCLUE3DHigh")
process.allTrackstersToSimTrackstersAssociationsByLCs = allTrackstersToSimTrackstersAssociationsByLCs
process.allTrackstersToSimTrackstersAssociationsByHits = allTrackstersToSimTrackstersAssociationsByHits
# SimCluster <-> CaloParticle association (SimCluster-seeded SimTracksters can be
# mapped to their CaloParticle through this)
process.SimClusterToCaloParticleAssociation = SimClusterToCaloParticleAssociation

process.ticlSimTrackstersTask = cms.Task(
    process.filteredLayerClustersSimTracksters,
    process.ticlSimTracksters,
    process.allLayerClusterToTracksterAssociations,
    process.allHitToTracksterAssociations,
    process.hitToSimClusterCaloParticleAssociator,
    process.allTrackstersToSimTrackstersAssociationsByLCs,
    process.allTrackstersToSimTrackstersAssociationsByHits,
    process.SimClusterToCaloParticleAssociation,
)
process.simTracksters = cms.Sequence(process.ticlSimTrackstersTask)

process.recosim_step *= process.simTracksters

# ---------------------------------------------------------------------------
# MC-truth graph (PhysicsTools/TruthInfo) as training truth.
# The graph and its hit index were built at DIGI by GSD_GUN.py (pileup-aware, under the
# era's enableTruth modifier) and are read from the input file. They are CONSUMED here,
# never rebuilt: a graph rebuilt at RECO would be signal-only (cms-truth docs, "Consume,
# do not rebuild"), so the guard below refuses a process that carries the producers.
#
# truthCaloBoundaryAssociators_cff matches the first TICL iteration (ticlTrackstersCLUE3DHigh
# in v5, ticlTrackstersCLUEstering in v6), in v6 also the final tracksters
# (ticlTracksterInterpretations), and hgcalMergeLayerClusters to the calo-boundary particles
# (truth::Level::CaloBoundary, one per outward tracker->calorimeter crossing, the SimCluster
# analogue; signal and in-time pile-up). Products, read by the nano step:
#   truthBranchTargetsCaloBoundary          "truthToRecoTargetsCaloBoundary": the target ids
#   truthCaloBoundaryTracksterAssociators   "<label>RecoToTruth{Fixed,AdaptiveTight,
#                                           AdaptiveNominal}" and "<label>TruthToReco" for
#                                           each trackster label above
#   truthCaloBoundaryLayerClusterAssociators the same four for hgcalMergeLayerClusters
# ---------------------------------------------------------------------------
for _m in ("truthGraphProducer", "truthLogicalGraphProducer", "truthLogicalGraphHitIndexProducer"):
    assert not hasattr(process, _m), (
        f"{_m} is scheduled in the RECO process: the MC-truth graph must be consumed from the "
        "GSD file, not rebuilt here (a rebuilt graph is signal-only)")

if options.truthAssoc:
    process.load("SimGeneral.TruthGraphAssociatorProducers.truthCaloBoundaryAssociators_cff")
    # The cff targets ticlTrackstersCLUE3DHigh, which TICLv6 does not run (the producer reads
    # its inputs with event.get, so a missing collection aborts the job): match the first
    # iteration of this reconstruction and, in v6, the final tracksters.
    process.truthCaloBoundaryTracksterAssociators.recoCollections = cms.VInputTag(*[
        cms.InputTag(_label) for _label in [_ticlFirstIteration, _ticlFinalTracksters] if _label])
    process.truthAssoc = cms.Sequence(process.truthCaloBoundaryAssociatorsSequence)
    process.recosim_step *= process.truthAssoc

if options.truthFullGraph:
    # The documented customise (customiseTruthGraphAssociators) also schedules the track,
    # vertex, PF-cluster and HLT domains, whose inputs this chain does not carry, so only
    # the trackster domain (every scheduled TICL trackster producer, found by type) and the
    # layer-cluster domain (hgcalMergeLayerClusters) are taken.
    # (setTracksterLabelsFromProcess cannot be used: it must run before the associator
    # cff is imported, which truthAssoc=1 already did; the labels are discovered the same
    # way and set on a clone instead.)
    from SimGeneral.TruthGraphAssociatorProducers.truthGraphAssociationLabels_cff import tracksterLabelsFromProcess
    from SimGeneral.TruthGraphAssociatorProducers.truthGraphAssociators_cff import (
        truthBranchTargets, truthBranchTracksterAssociators, truthBranchLayerClusterAssociators)
    _tracksterLabels = tracksterLabelsFromProcess(process) or [_ticlFirstIteration]
    # Pile-up particles are level targets too and no pt floor, as for the calo-boundary
    # targets (the stock defaults are the signal-only validation denominators).
    process.truthBranchTargets = truthBranchTargets.clone(
        truthToRecoSignalOnly=False,
        branchSelector=dict(ptMin=0.0),
    )
    process.truthBranchTracksterAssociators = truthBranchTracksterAssociators.clone(
        recoCollections=[cms.InputTag(l) for l in _tracksterLabels],
        pfRecHits=cms.VInputTag())
    # hgcalMergeLayerClusters against the same full-graph targets (stock defaults).
    process.truthBranchLayerClusterAssociators = truthBranchLayerClusterAssociators.clone()
    process.truthFullGraphAssoc = cms.Sequence(process.truthBranchTargets
                                               + process.truthBranchTracksterAssociators
                                               + process.truthBranchLayerClusterAssociators)
    process.recosim_step *= process.truthFullGraphAssoc

# Print out event content for debugging
#process.dump=cms.EDAnalyzer('EventContentAnalyzer')
#process.recosim_step += process.dump

# Input source
process.source.fileNames = cms.untracked.vstring(options.inputFiles)

# Output definition
process.FEVTDEBUGoutput.fileName = cms.untracked.string(
    options.__getattr__("outputFile", noTags=True))
process.FEVTDEBUGoutput.outputCommands.append("keep *_*G4*_*_*")
process.FEVTDEBUGoutput.outputCommands.extend(["keep *_MergedTrackTruth_*_*",
    "keep *_trackingParticleRecoTrackAsssociation_*_*", 
    "keep *_hgcRecHitsToSimClusters_*_*", 
    "keep SimClustersedmAssociation_mix_*_*", "keep CaloParticlesedmAssociation_mix_*_*", 
    "keep *_pfParticles_*_*",
    "keep recoPFRecHits_*_*_*", 
    "keep *_hgcSimTruth_*_*",
    "keep *_lcAssocByEnergyScoreProcer_*_*",
    "keep *_layerClusterCaloParticleAssociationProducer_*_*",
    "keep *_scAssocByEnergyScoreProducer_*_*",
    "keep *_layerClusterSimClusterAssociationProducer_*_*",
    "keep *_filteredLayerClusters*_*_*",
    "keep *_hgcalMergeLayerClusters_*_*",
    "keep *_ticlSeeding*_*_*",
    "keep *_ticlTracksters*_*_*",
    "keep *_ticlCandidate*_*_*",
    "keep *_muons*_*_*",
    "keep *_simTrack*_*_*",
    "keep *_simHit*_*_*",
    "keep *_mix_MergedCaloTruth*_*",
    # The MC-truth graph products (logical graph, hit index, raw merged graph) are added
    # below by setTruthEventContent; the reco -> truth association maps here.
    "keep *_truthBranchTargetsCaloBoundary_*_*",
    "keep *_truthCaloBoundaryTracksterAssociators_*_*",
    "keep *_truthCaloBoundaryLayerClusterAssociators_*_*",
    "keep *_truthBranchTargets_*_*",
    "keep *_truthBranchTracksterAssociators_*_*",
    "keep *_truthBranchLayerClusterAssociators_*_*",
    # TICLv6 (useTICL=1). The CLUEstering iteration is matched by ticlTracksters* above; the
    # interpretation stage is kept by the TICL event content, which then drops its
    # per-trackster species and type probabilities (they serve ticlCandidate in the same job).
    # Kept again here (last match wins) as the PID of the final tracksters. The device-side
    # CLUEstering products (hgcalSoARecHits*, hgcalCLUEstering*, hgcalSoALayerClusters*,
    # hgcalSoAMergeLayerClusters, ticlCLUEstering, ticlSoATracksters) stay dropped: their
    # content is the one of hgcalMergeLayerClusters and ticlTrackstersCLUEstering.
    "keep *_ticlTrackstersCLUEstering_*_*",
    "keep *_ticlTracksterInterpretations_*_*",
    # MC-truth tracksters and their associations (see block above). The
    # FEVTDEBUG content already lists most of these;
    # they are repeated here so the file documents what nanoML relies on.
    # Since CMSSW_20_0_0_pre1 the sim TICLCandidates and the fromCPs tracksters are
    # instances of the ticlSimTracksters module, so the first line already covers
    # them; the next two only matter if they become separate modules upstream.
    "keep *_ticlSimTracksters_*_*",
    "keep *_ticlSimTICLCandidates_*_*",
    "keep *_ticlSimTrackstersFromCP_*_*",
    "keep *_allTrackstersToSimTrackstersAssociations*_*_*",
    "keep *_allLayerClusterToTracksterAssociations_*_*",
    "keep *_SimClusterToCaloParticleAssociation_*_*",
])

# Carry the DIGI-built truth (truthLogicalGraphProducer, truthLogicalGraphHitIndexProducer,
# TruthGraph_mix) through to the RECO file: the nano step reads the graph and the hit index.
from PhysicsTools.TruthInfo.truthEventContent_cff import setTruthEventContent
process = setTruthEventContent(process)

process.options.numberOfThreads=cms.untracked.uint32(options.nThreads)

if hasattr(process, "DQMoutput"):
    process.DQMoutput.fileName = cms.untracked.string(options.outputFileDQM)

