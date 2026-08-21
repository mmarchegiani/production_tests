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
    "use TICL v5 (1=True, 0=False)")
options.parseArguments()

# Import process based on useTICL flag
if options.useTICL:
    from reco_prodtools.templates.RECO_fragment import process  # ticl_v5 no longer exists; TICL is default in pre1
else:
    from reco_prodtools.templates.RECO_fragment import process

process.maxEvents.input = cms.untracked.int32(options.maxEvents)

process.load("SimTracker.TrackAssociation.trackingParticleRecoTrackAsssociation_cfi")
# append the HGCTruthProducer to the recosim step

# If you want to run the associations or SC merging
from SimCalorimetry.HGCalSimProducers.hgcHitAssociation_cfi import lcAssocByEnergyScoreProducer, scAssocByEnergyScoreProducer
from SimCalorimetry.HGCalAssociatorProducers.LCToCPAssociation_cfi import layerClusterCaloParticleAssociation as layerClusterCaloParticleAssociationProducer
from SimCalorimetry.HGCalAssociatorProducers.LCToSCAssociation_cfi import layerClusterSimClusterAssociation as layerClusterSimClusterAssociationProducer
from SimGeneral.TrackingAnalysis.simHitTPAssociation_cfi import simHitTPAssocProducer

process.lcAssocByEnergyScoreProducer = lcAssocByEnergyScoreProducer
process.layerClusterCaloParticleAssociationProducer = layerClusterSimClusterAssociationProducer
process.scAssocByEnergyScoreProducer = scAssocByEnergyScoreProducer
process.layerClusterSimClusterAssociationProducer = layerClusterCaloParticleAssociationProducer
process.simHitTPAssocProducer = simHitTPAssocProducer

process.hgcalAssociators = cms.Task(
    process.lcAssocByEnergyScoreProducer,
    process.layerClusterCaloParticleAssociationProducer,
    process.scAssocByEnergyScoreProducer,
    process.layerClusterSimClusterAssociationProducer,
    process.trackingParticleRecoTrackAsssociation,
    process.simHitTPAssocProducer
)

process.assoc = cms.Sequence(process.hgcalAssociators)

process.recosim_step *= process.assoc

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
])

process.options.numberOfThreads=cms.untracked.uint32(options.nThreads)

if hasattr(process, "DQMoutput"):
    process.DQMoutput.fileName = cms.untracked.string(options.outputFileDQM)

