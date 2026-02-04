import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from Configuration.Eras.Era_Phase2C17I13M9_cff import Phase2C17I13M9
from Configuration.ProcessModifiers.ticl_v5_cff import ticl_v5

process = cms.Process('TICL', Phase2C17I13M9, ticl_v5)

# option parsing
options = VarParsing('python')
options.setDefault('outputFile', 'testNanoTICL.root')
options.register("nThreads", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int, "number of threads")
options.parseArguments()

# Load necessary reconstruction modules
process.load('Configuration.StandardSequences.Services_cff')
process.load('Configuration.StandardSequences.MagneticField_cff')
process.load('Configuration.StandardSequences.Reconstruction_cff')
process.load('Configuration.StandardSequences.FrontierConditions_GlobalTag_cff')
process.load('Configuration.Geometry.GeometryExtendedRun4D110Reco_cff')
process.load('Configuration.EventContent.EventContent_cff')
process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
process.load('FWCore.MessageService.MessageLogger_cfi')
process.load('SimGeneral.MixingModule.mixNoPU_cfi')
process.load('Configuration.Geometry.GeometryExtendedRun4D110_cff')
process.load('DPGAnalysis.HGCalNanoAOD.nanoHGCML_cff')
process.load('SimCalorimetry.HGCalSimProducers.hgcHitAssociation_cfi')
process.load('SimCalorimetry.HGCalAssociatorProducers.LCToTSAssociator_cfi')
process.load('SimCalorimetry.HGCalAssociatorProducers.HitToTracksterAssociation_cfi')
process.load('SimCalorimetry.HGCalAssociatorProducers.hitToSimClusterCaloParticleAssociator_cfi')
process.load('SimCalorimetry.HGCalAssociatorProducers.SimClusterToCaloParticleAssociation_cfi')
process.load('RecoHGCal.TICL.ticlLayerTileProducer_cfi')
process.load('RecoHGCal.TICL.trackstersProducer_cfi')
process.load('RecoHGCal.TICL.filteredLayerClustersProducer_cfi')
process.load('RecoHGCal.TICL.SimTracksters_cff')

# Fix for ProductNotFound error with FlatEtaRangeGunProducer
process.tpClusterProducer.pixelSimLinkSrc = cms.InputTag("simSiPixelDigis", "Pixel")
process.tpClusterProducer.phase2OTSimLinkSrc = cms.InputTag("simSiPixelDigis", "Tracker")

from Configuration.AlCa.GlobalTag import GlobalTag
process.GlobalTag = GlobalTag(process.GlobalTag, 'auto:phase2_realistic', '')

from RecoTracker.IterativeTracking.iterativeTk_cff import trackdnn_source
from SimCalorimetry.HGCalAssociatorProducers.LCToCPAssociation_cfi import layerClusterCaloParticleAssociation as layerClusterCaloParticleAssociationProducer
from SimCalorimetry.HGCalAssociatorProducers.LCToSCAssociation_cfi import layerClusterSimClusterAssociation as layerClusterSimClusterAssociationProducer

from RecoHGCal.TICL.customiseForTICLv5_cff import *
from RecoHGCal.TICL.ticlDumper_cff import ticlDumper
#from RecoHGCal.TICL.customiseTICLFromReco import customiseTICLForDumper


# Input source
process.source = cms.Source("PoolSource",
    fileNames = cms.untracked.vstring(options.inputFiles),
    secondaryFileNames = cms.untracked.vstring()
)

process.maxEvents = cms.untracked.PSet(
    input = cms.untracked.int32(-1)
)

process.trackdnn_source = trackdnn_source
process.layerClusterCaloParticleAssociationProducer = layerClusterCaloParticleAssociationProducer
process.layerClusterSimClusterAssociationProducer = layerClusterSimClusterAssociationProducer

# Debug output
process.FEVTDEBUGHLToutput = cms.OutputModule("PoolOutputModule",
    dataset = cms.untracked.PSet(
        dataTier = cms.untracked.string('GEN-SIM-RECO'),
        filterName = cms.untracked.string('')
    ),
    fileName = cms.untracked.string('file:output_with_ticlcandidates.root'),
    outputCommands = process.FEVTDEBUGHLTEventContent.outputCommands,
    splitLevel = cms.untracked.int32(0)
)

# Apply TICLv5 customization
process = customiseTICLv5FromReco(process, enableDumper=True)

# Apply nanoHGCML customizations for MergedSimClusters
from DPGAnalysis.HGCalNanoAOD.nanoHGCML_cff import customizeReco, customizeMergedSimClusters
process = customizeMergedSimClusters(process)
process = customizeReco(process)

# Configure the TICL dumper to save desired information
#from RecoHGCal.TICL.customiseTICLFromReco import customiseTICLForDumper
#process = customiseTICLForDumper(process, histoName = "histo.root")
#process.ticlDumper.saveLCs = True
#process.ticlDumper.saveTICLCandidate = True
#process.ticlDumper.saveSimTICLCandidate = True
#process.ticlDumper.saveTracks = True
#process.ticlDumper.saveSuperclustering = True
#process.ticlDumper.saveRecoSuperclusters = True

# TICLDumper
process.ticlDumper = ticlDumper.clone(
    saveLCs=True,
    saveTICLCandidate=True,
    saveSimTICLCandidate=True,
    saveTracks=True,
    saveSuperclustering=False,
    saveRecoSuperclusters=False,
)
process.TFileService = cms.Service("TFileService",
                                    fileName=cms.string("histo.root")
                                    )
process.ticlDumper_step = cms.EndPath(process.ticlDumper)

# Add nanoHGCML sequence for MergedSimClusters (will be saved to NanoAOD output)
process.nanoHGCML_step = cms.Path(process.nanoHGCMLSequence)

# Add steps to schedule
process.schedule.append(process.nanoHGCML_step)
process.schedule.append(process.ticlDumper_step)

# Output
process.NANOAODSIMoutput = cms.OutputModule("NanoAODOutputModule",
    compressionAlgorithm = cms.untracked.string('LZMA'),
    compressionLevel = cms.untracked.int32(9),
    dataset = cms.untracked.PSet(
        dataTier = cms.untracked.string('NANOAODSIM'),
        filterName = cms.untracked.string('')
    ),
    fileName = cms.untracked.string('file:nanoAOD_with_ticlcandidates.root'),
    outputCommands = process.NANOAODSIMEventContent.outputCommands
)

process.NANOAODSIMoutput.outputCommands.remove("keep edmTriggerResults_*_*_*")

# Path and EndPath definitions
process.NANOAODSIMoutput_step = cms.EndPath(process.NANOAODSIMoutput)

process.schedule.append(process.NANOAODSIMoutput_step)

# Helper to associate tools
from PhysicsTools.PatAlgos.tools.helpers import associatePatAlgosToolsTask
associatePatAlgosToolsTask(process)

# Add early deletion to reduce memory usage
from Configuration.StandardSequences.earlyDeleteSettings_cff import customiseEarlyDelete
process = customiseEarlyDelete(process)

