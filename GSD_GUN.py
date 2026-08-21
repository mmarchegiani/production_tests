# coding: utf-8

import os
import math

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing

# option parsing
options = VarParsing('python')
options.setDefault('outputFile', 'file:partGun_PDGid22_x96_Pt1.0To100.0_GSD_1.root')
options.setDefault('maxEvents', 10)
options.register("pileup", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "average pileup (0 = no PU). Requires pu=<minbias.root> when > 0.")
options.register("seed", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "random seed")
options.register("nThreads", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "number of threads")
options.register("nParticles", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "number of particles in gun")
options.register("useFineCalo", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "use fine calorimeter segmentation (1=True, 0=False)")
options.register("particle", 22, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "pdgId of the particle to shoot (22=photon, 11=electron, 211=pi+, 130=K0L, 15=tau, ...)")
options.register("energy", 100.0, VarParsing.multiplicity.singleton, VarParsing.varType.float,
    "gun energy in GeV (fixed; MinE=MaxE=energy)")
options.register("pu", "", VarParsing.multiplicity.singleton, VarParsing.varType.string,
    "path to a minbias GEN-SIM file to mix as pileup (from MINBIAS_GENSIM.py); "
    "required when pileup>0.")
options.parseArguments()

# Import process based on useFineCalo and pileup flags.
# - useFineCalo=1: use GSDfineCalo_fragment (no PU support here)
# - pileup > 0:    use GSD_fragment_PU (built with cmsDriver --pileup AVE_30_BX_25ns
#                  --pileup_input das:...). Its mix module already has the
#                  PU-aware digi wiring; we just override the placeholder
#                  DAS input with the local minbias file below.
# - otherwise:     use GSD_fragment (mixNoPU baked in).
if options.useFineCalo:
    from reco_prodtools.templates.GSDfineCalo_fragment import process
elif options.pileup > 0:
    from reco_prodtools.templates.GSD_fragment_PU import process
else:
    from reco_prodtools.templates.GSD_fragment import process

process.maxEvents.input = cms.untracked.int32(options.maxEvents)

# MergedCaloTruthMergedSimCluster (PR #50578 CaloTruthAccumulator collections).
# Both flags already default to True in
# SimGeneral/MixingModule/python/caloTruthProducer_cfi.py, and
# SaveCaloBoundaryInformation is already forced True for all Phase-2 workflows
# by the phase2_hgcal era modifier (SimG4Core/Application/python/g4SimHits_cfi.py).
# Pinned explicitly here so production doesn't silently lose this collection if
# an upstream default changes.
process.mix.digitizers.calotruth.produceLegacySimCluster = cms.bool(True)
process.mix.digitizers.calotruth.produceBoundaryAndMergedSimCluster = cms.bool(True)
process.g4SimHits.TrackingAction.SaveCaloBoundaryInformation = cms.bool(True)

# Retain the new mix:MergedCaloTruth* SimCluster/CaloParticle instances
# (MergedCaloTruthBoundaryTrackSimCluster, MergedCaloTruthMergedSimCluster,
# MergedCaloTruthCaloParticle) through to the GSD output. Already covered by
# the standard phase2_hgcal event content wildcard; kept explicit here too.
process.FEVTDEBUGoutput.outputCommands.append("keep *_mix_MergedCaloTruth*_*")

seed = int(options.seed) + 1
# random seeds
process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(seed)
process.RandomNumberGeneratorService.VtxSmeared.initialSeed = cms.untracked.uint32(seed)
process.RandomNumberGeneratorService.mix.initialSeed = cms.untracked.uint32(seed)

# Input source
process.source.firstLuminosityBlock = cms.untracked.uint32(seed)

# Output definition
process.FEVTDEBUGoutput.fileName = cms.untracked.string(
    options.__getattr__("outputFile", noTags=True))

process.FEVTDEBUGoutput.outputCommands.append("keep *_*G4*_*_*")
process.FEVTDEBUGoutput.outputCommands.append("keep SimClustersedmAssociation_mix_*_*")
process.FEVTDEBUGoutput.outputCommands.append("keep CaloParticlesedmAssociation_mix_*_*")


# helper
def calculate_rho(z, eta):
    return z * math.tan(2 * math.atan(math.exp(-eta)))


process.generator = cms.EDProducer("edm::FlatEtaRangeGunProducer",
    PGunParameters = cms.PSet(
        # particle id (configurable via --particle)
        PartID=cms.vint32(int(options.particle)),
        # max number of particles to shoot at a time
        nParticles=cms.int32(options.nParticles),
        # shoot exactly the particles defined in particleIDs in that order
        exactShoot=cms.bool(False),
        # randomly shoot [1, nParticles] particles, each time randomly drawn
        randomShoot=cms.bool(False),
        # energy range (fixed; MinE=MaxE=options.energy)
        MinE=cms.double(float(options.energy)),
        MaxE=cms.double(float(options.energy)),
        # phi range
        MinPhi=cms.double(-math.pi),
        MaxPhi=cms.double(math.pi),
        # eta range
        MinEta=cms.double(1.7),
        MaxEta=cms.double(2.7),
    ),
    AddAntiParticle=cms.bool(False),
    debug=cms.untracked.bool(True),
    firstRun=cms.untracked.uint32(1)
)

process.options.numberOfThreads = cms.untracked.uint32(options.nThreads)

# Pileup configuration.
# When pileup > 0, GSD_fragment_PU is imported above. Its mix module is
# already configured for PU digi with a placeholder DAS input; we swap in
# the local minbias GENSIM file (from MINBIAS_GENSIM.py) and set the
# averageNumber from the CLI. When pileup == 0 the non-PU fragment ships
# with mixNoPU_cfi wired in; nothing to do.
if options.pileup > 0:
    if not options.pu:
        raise RuntimeError(
            "pileup > 0 requires pu=<minbias.root> to be set. "
            "Generate the minbias file with MINBIAS_GENSIM.py first."
        )
    pu_uri = options.pu if (":" in options.pu) else ("file:" + options.pu)
    process.mix.input.fileNames = cms.untracked.vstring([pu_uri])
    process.mix.input.nbPileupEvents.averageNumber = cms.double(options.pileup)
