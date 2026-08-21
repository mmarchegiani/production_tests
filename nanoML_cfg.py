# Auto generated configuration file
# using: 
# Revision: 1.19 
# Source: /local/reps/CMSSW/CMSSW/Configuration/Applications/python/ConfigBuilder.py,v 
# with command line options: step1 --filein file:test.root --fileout testNanoML.root --mc --eventcontent NANOAODSIM --datatier NANOAODSIM --conditions auto:mc --step NANO
import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing


process = cms.Process('NANO')
options = VarParsing('python')
options.setDefault('outputFile', 'testNanoML.root')
options.register("nThreads", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "number of threads")
options.register("runPFTruth", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "Don't run PFTruth (currently not working with pileup)")
options.parseArguments()

# import of standard configurations
process.load('Configuration.StandardSequences.Services_cff')
process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
process.load('FWCore.MessageService.MessageLogger_cfi')
process.load('Configuration.EventContent.EventContent_cff')
process.load('SimGeneral.MixingModule.mixNoPU_cfi')
process.load('Configuration.Geometry.GeometryExtendedRun4D121Reco_cff')
process.load('Configuration.Geometry.GeometryExtendedRun4D121_cff')
process.load('Configuration.StandardSequences.MagneticField_cff')
process.load('DPGAnalysis.HGCalNanoAOD.nanoHGCML_cff')

# specify where to look for simSiPixelDigis objects
process.tpClusterProducer.pixelSimLinkSrc = cms.InputTag("simSiPixelDigis", "Pixel", "HLT")
process.tpClusterProducer.phase2OTSimLinkSrc = cms.InputTag("simSiPixelDigis", "Tracker", "HLT")

process.load('Configuration.StandardSequences.Reconstruction_cff')
process.load('Configuration.StandardSequences.EndOfProcess_cff')
process.load('Configuration.StandardSequences.FrontierConditions_GlobalTag_cff')

# This isn't working with pileup
if not options.runPFTruth:
    process.pfTruth = cms.Sequence()
    process.trackSCAssocTable = cms.Sequence()

process.maxEvents = cms.untracked.PSet(
    input = cms.untracked.int32(-1),
    output = cms.optional.untracked.allowed(cms.int32,cms.PSet)
)
process.options.numberOfThreads=cms.untracked.uint32(options.nThreads)

# Input source
process.source = cms.Source("PoolSource",
    fileNames = cms.untracked.vstring(options.inputFiles),
    secondaryFileNames = cms.untracked.vstring()
)

process.options = cms.untracked.PSet(
    IgnoreCompletely = cms.untracked.vstring(),
    Rethrow = cms.untracked.vstring(),
    allowUnscheduled = cms.obsolete.untracked.bool,
    canDeleteEarly = cms.untracked.vstring(),
    emptyRunLumiMode = cms.obsolete.untracked.string,
    eventSetup = cms.untracked.PSet(
        forceNumberOfConcurrentIOVs = cms.untracked.PSet(
            allowAnyLabel_=cms.required.untracked.uint32
        ),
        numberOfConcurrentIOVs = cms.untracked.uint32(1)
    ),
    fileMode = cms.untracked.string('FULLMERGE'),
    forceEventSetupCacheClearOnNewRun = cms.untracked.bool(False),
    makeTriggerResults = cms.obsolete.untracked.bool,
    numberOfConcurrentLuminosityBlocks = cms.untracked.uint32(1),
    numberOfConcurrentRuns = cms.untracked.uint32(1),
    numberOfStreams = cms.untracked.uint32(0),
    numberOfThreads = cms.untracked.uint32(1),
    printDependencies = cms.untracked.bool(False),
    sizeOfStackForThreadsInKB = cms.optional.untracked.uint32,
    throwIfIllegalParameter = cms.untracked.bool(True),
    wantSummary = cms.untracked.bool(False),
    TryToContinue = cms.untracked.vstring('ProductNotFound') #continue even if a product is not found
)

# Production Info
process.configurationMetadata = cms.untracked.PSet(
    annotation = cms.untracked.string('step1 nevts:1'),
    name = cms.untracked.string('Applications'),
    version = cms.untracked.string('$Revision: 1.19 $')
)

# Output definition

process.NANOAODSIMoutput = cms.OutputModule("NanoAODOutputModule",
    compressionAlgorithm = cms.untracked.string('LZMA'),
    compressionLevel = cms.untracked.int32(9),
    dataset = cms.untracked.PSet(
        dataTier = cms.untracked.string('NANOAODSIM'),
        filterName = cms.untracked.string('')
    ),
    fileName = cms.untracked.string(options.__getattr__("outputFile", noTags=True)),
    outputCommands = process.NANOAODSIMEventContent.outputCommands
)

process.NANOAODSIMoutput.outputCommands.remove("keep edmTriggerResults_*_*_*")
# Keep the TICLCand table produced by ticl_step (not in default NANOAODSIMEventContent).
process.NANOAODSIMoutput.outputCommands.append("keep nanoaodFlatTable_ticl*_*_*")

# Additional output definition

# Other statements
from Configuration.AlCa.GlobalTag import GlobalTag
process.GlobalTag = GlobalTag(process.GlobalTag, 'auto:mc', '')

# TICL nano tables (added for CMSSW_20_0_0_pre1 / D121).
#
# TICL is the default reco in pre1. Its products (ticlCandidate,
# ticlTrackstersCLUE3DHigh, ticlTracksterLinks, ticlTrackstersRecovery,
# hgcalMergeLayerClusters) are always in RECO output.
#
# The plugins that emit nano tables for these are in HLTrigger/NGTScouting
# (originally written for HLT scouting) and are already registered in
# pluginHLTriggerNGTScoutingAuto.so. We wire them here with offline input
# tags. This gives us the full offline chain:
#     RecHit -> LayerCluster  (RecHitHGC_LayerCluster_MatchIdx, in nano)
#     LayerCluster -> Trackster  (via tracksterVertices in trackster table)
#     Trackster -> TICLCand  (via Candidate2TrackstersIndices)
process.ticlCandidateTable = cms.EDProducer("TICLCandidateTableProducer",
    skipNonExistingSrc=cms.bool(True),
    src=cms.InputTag("ticlCandidate"),
    cut=cms.string(""),
    name=cms.string("TICLCand"),
    doc=cms.string("Offline TICLCandidates (from ticlCandidate producer)"),
    singleton=cms.bool(False),
    variables=cms.PSet(
        raw_energy=cms.PSet(expr=cms.string("rawEnergy"), type=cms.string("float"), doc=cms.string("Raw energy [GeV]"), precision=cms.int32(-1)),
        pt=cms.PSet(expr=cms.string("pt"), type=cms.string("float"), doc=cms.string("pT [GeV]"), precision=cms.int32(-1)),
        p=cms.PSet(expr=cms.string("p"), type=cms.string("float"), doc=cms.string("|p| [GeV]"), precision=cms.int32(-1)),
        energy=cms.PSet(expr=cms.string("energy"), type=cms.string("float"), doc=cms.string("Energy [GeV]"), precision=cms.int32(-1)),
        eta=cms.PSet(expr=cms.string("eta"), type=cms.string("float"), doc=cms.string("eta"), precision=cms.int32(-1)),
        phi=cms.PSet(expr=cms.string("phi"), type=cms.string("float"), doc=cms.string("phi"), precision=cms.int32(-1)),
        mass=cms.PSet(expr=cms.string("mass"), type=cms.string("float"), doc=cms.string("mass"), precision=cms.int32(-1)),
        pdgID=cms.PSet(expr=cms.string("pdgId"), type=cms.string("int"), doc=cms.string("pdgId"), precision=cms.int32(-1)),
        charge=cms.PSet(expr=cms.string("charge"), type=cms.string("int"), doc=cms.string("charge"), precision=cms.int32(-1)),
        time=cms.PSet(expr=cms.string("time"), type=cms.string("float"), doc=cms.string("HGCAL time"), precision=cms.int32(-1)),
        timeError=cms.PSet(expr=cms.string("timeError"), type=cms.string("float"), doc=cms.string("HGCAL time error"), precision=cms.int32(-1)),
    ),
)

# TICLCand -> linked Tracksters (Candidate2TrackstersIndices)
process.ticlCandidateExtraTable = cms.EDProducer("TICLCandidateExtraTableProducer",
    src=cms.InputTag("ticlCandidate"),
    name=cms.string("Candidate2Tracksters"),
    skipNonExistingSrc=cms.bool(True),
    doc=cms.string("TICLCandidates extra table with linked Tracksters"),
    collectionVariables=cms.PSet(
        tracksters=cms.PSet(
            name=cms.string("Candidate2TrackstersIndices"),
            doc=cms.string("Tracksters linked to TICLCandidates"),
            useCount=cms.bool(True),
            useOffset=cms.bool(False),
            variables=cms.PSet(),
        ),
    ),
)

# Trackster tables for each offline TICL iteration: gives us the
# LayerCluster<->Trackster link via the `vertices` collection variable.
_ticlIterLabels = ["ticlTrackstersCLUE3DHigh", "ticlTrackstersRecovery",
                   "ticlTracksterLinks", "ticlTracksterLinksSuperclusteringDNN"]

_tracksterTableProducers = []
for _iterLabel in _ticlIterLabels:
    _prod = cms.EDProducer("TracksterCollectionTableProducer",
        skipNonExistingSrc=cms.bool(True),
        src=cms.InputTag(_iterLabel),
        cut=cms.string(""),
        name=cms.string(_iterLabel),
        doc=cms.string(_iterLabel),
        singleton=cms.bool(False),
        variables=cms.PSet(
            raw_energy=cms.PSet(expr=cms.string("raw_energy"), type=cms.string("float"), doc=cms.string("Raw energy [GeV]"), precision=cms.int32(-1)),
            raw_em_energy=cms.PSet(expr=cms.string("raw_em_energy"), type=cms.string("float"), doc=cms.string("EM raw energy [GeV]"), precision=cms.int32(-1)),
            raw_pt=cms.PSet(expr=cms.string("raw_pt"), type=cms.string("float"), doc=cms.string("Raw pT [GeV]"), precision=cms.int32(-1)),
            regressed_energy=cms.PSet(expr=cms.string("regressed_energy"), type=cms.string("float"), doc=cms.string("Regressed energy"), precision=cms.int32(-1)),
            barycenter_x=cms.PSet(expr=cms.string("barycenter.x"), type=cms.string("float"), doc=cms.string("Barycenter x [cm]"), precision=cms.int32(-1)),
            barycenter_y=cms.PSet(expr=cms.string("barycenter.y"), type=cms.string("float"), doc=cms.string("Barycenter y [cm]"), precision=cms.int32(-1)),
            barycenter_z=cms.PSet(expr=cms.string("barycenter.z"), type=cms.string("float"), doc=cms.string("Barycenter z [cm]"), precision=cms.int32(-1)),
            barycenter_eta=cms.PSet(expr=cms.string("barycenter.eta"), type=cms.string("float"), doc=cms.string("Barycenter eta"), precision=cms.int32(-1)),
            barycenter_phi=cms.PSet(expr=cms.string("barycenter.phi"), type=cms.string("float"), doc=cms.string("Barycenter phi"), precision=cms.int32(-1)),
            time=cms.PSet(expr=cms.string("time"), type=cms.string("float"), doc=cms.string("HGCAL time"), precision=cms.int32(-1)),
            timeError=cms.PSet(expr=cms.string("timeError"), type=cms.string("float"), doc=cms.string("HGCAL time error"), precision=cms.int32(-1)),
        ),
        collectionVariables=cms.PSet(
            tracksterVertices=cms.PSet(
                name=cms.string(_iterLabel + "vertices"),
                doc=cms.string("Trackster<->LayerCluster association (vertex = LC index)"),
                useCount=cms.bool(True),
                useOffset=cms.bool(True),
                variables=cms.PSet(
                    vertices=cms.PSet(expr=cms.string("vertices"), type=cms.string("uint"), doc=cms.string("LayerCluster index"), precision=cms.int32(-1)),
                    vertex_mult=cms.PSet(expr=cms.string("vertex_multiplicity"), type=cms.string("float"), doc=cms.string("Fraction of LC energy used by trackster"), precision=cms.int32(-1)),
                ),
            ),
        ),
    )
    _attrName = _iterLabel + "Table"
    setattr(process, _attrName, _prod)
    _tracksterTableProducers.append(getattr(process, _attrName))

# All TICL nano producers get scheduled as a single Task attached to nanoAOD_step
process.ticlTablesTask = cms.Task(
    process.ticlCandidateTable,
    process.ticlCandidateExtraTable,
    *_tracksterTableProducers,
)

# MergedCaloTruthMergedSimCluster nano table (PR #50578 CaloTruthAccumulator
# collection). Boundary-crossing SimTracks are
# turned into per-CaloParticle SimClusters and merged via anti-kt(R=0.05)
# FastJet clustering, requiring SaveCaloBoundaryInformation (pinned in
# GSD_GUN.py). Same SimClusterCollection C++ type as mix:MergedCaloTruth, so
# this reuses simClusterTable's variable set as-is.
process.mergedCaloTruthMergedSimClusterTable = process.simClusterTable.clone(
    src=cms.InputTag("mix", "MergedCaloTruthMergedSimCluster"),
    name=cms.string("MergedCaloTruthMergedSimCluster"),
    doc=cms.string("Boundary-track SimClusters merged per-CaloParticle via "
                   "anti-kt(R=0.05) (requires SaveCaloBoundaryInformation)"),
)

process.hgcRecHitsToMergedCaloTruthMergedSimClusters = cms.EDProducer("SimClusterRecHitAssociationProducer",
    caloRecHits=cms.VInputTag("hgcRecHits"),
    simClusters=cms.InputTag("mix", "MergedCaloTruthMergedSimCluster"),
)

process.mergedCaloTruthMergedSimClusterRecEnergyTable = cms.EDProducer("SimClusterRecEnergyTableProducer",
    src=cms.InputTag("mix", "MergedCaloTruthMergedSimCluster"),
    cut=cms.string(""),
    objName=cms.string("MergedCaloTruthMergedSimCluster"),
    branchName=cms.string("recEnergy"),
    valueMap=cms.InputTag("hgcRecHitsToMergedCaloTruthMergedSimClusters"),
    docString=cms.string("MergedCaloTruthMergedSimCluster deposited reconstructed energy"),
)

process.hgcRecHitsToMergedCaloTruthMergedSimClusterTable = cms.EDProducer("HGCRecHitToSimClusterIndexTableProducer",
    cut=process.hgcRecHitsTable.cut,
    src=process.hgcRecHitsTable.src,
    objName=process.hgcRecHitsTable.name,
    branchName=cms.string("MergedCaloTruthMergedSimCluster"),
    objMap=cms.InputTag("hgcRecHitsToMergedCaloTruthMergedSimClusters", "hgcRecHitsToSimClus"),
    bestMatchTable=cms.untracked.bool(True),
    docString=cms.string("MergedCaloTruthMergedSimCluster ordered by most sim energy in RecHit DetId"),
)

process.mergedCaloTruthMergedSimClusterTask = cms.Task(
    process.mergedCaloTruthMergedSimClusterTable,
    process.hgcRecHitsToMergedCaloTruthMergedSimClusters,
    process.mergedCaloTruthMergedSimClusterRecEnergyTable,
    process.hgcRecHitsToMergedCaloTruthMergedSimClusterTable,
)

# Path and EndPath definitions
process.nanoAOD_step = cms.Path(process.nanoHGCMLSequence, process.ticlTablesTask,
                                 process.mergedCaloTruthMergedSimClusterTask)

process.endjob_step = cms.EndPath(process.endOfProcess)
process.NANOAODSIMoutput_step = cms.EndPath(process.NANOAODSIMoutput)

#omit genIso objects to avoid product not found error (objects only available at mininanoaod step)
if hasattr(process, 'genParticleTable'):
    # Remove the 'iso' variable from the table configuration
    if hasattr(process.genParticleTable.externalVariables, 'iso'):
        del process.genParticleTable.externalVariables.iso

# Schedule definition
process.schedule = cms.Schedule(process.nanoAOD_step,process.endjob_step,process.NANOAODSIMoutput_step)
from PhysicsTools.PatAlgos.tools.helpers import associatePatAlgosToolsTask
associatePatAlgosToolsTask(process)

# customisation of the process.
from DPGAnalysis.HGCalNanoAOD.nanoHGCML_cff import customizeReco,customizeMergedSimClusters
# Uncomment if you didn't schedule SimClusters/CaloParticles
# process = customizeNoMergedCaloTruth(process)
# merged simclusters (turn off if you aren't running through PEPR)
process = customizeMergedSimClusters(process)
process = customizeReco(process)

# End of customisation functions


# Customisation from command line

# Add early deletion of temporary data products to reduce peak memory need
from Configuration.StandardSequences.earlyDeleteSettings_cff import customiseEarlyDelete
process = customiseEarlyDelete(process)
# End adding early deletion
