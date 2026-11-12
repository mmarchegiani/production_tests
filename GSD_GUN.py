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
options.register("minE", 20.0, VarParsing.multiplicity.singleton, VarParsing.varType.float,
    "gun minimum energy in GeV")
options.register("maxE", 200.0, VarParsing.multiplicity.singleton, VarParsing.varType.float,
    "gun maximum energy in GeV")
options.register("pu", "", VarParsing.multiplicity.singleton, VarParsing.varType.string,
    "path to a minbias GEN-SIM file to mix as pileup (from MINBIAS_GENSIM.py); "
    "required when pileup>0.")
options.register("gun", "flatEta", VarParsing.multiplicity.singleton, VarParsing.varType.string,
    "particle gun: flatEta (from the origin, nParticles into each endcap, |eta| 1.5-3.0) "
    "or closeBy (nParticles starting on the +z HGCal front face, R 60-120 cm, z 320-321 cm, "
    "consecutive particles delta cm apart)")
options.register("delta", 10.0, VarParsing.multiplicity.singleton, VarParsing.varType.float,
    "closeBy gun only: arc-length separation in cm between consecutive particles on the HGCal "
    "front face (Delta of CloseByParticleGunProducer; 10 cm is the producer's default). Every "
    "particle after the first sits at the same R and z, shifted in phi by delta/R, so with "
    "nParticles=2 the two showers start exactly delta cm apart. Not accepted with gun=flatEta.")
options.register("truthReduced", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "MC-truth graph scope: 0 = full detector (calo + MTD + muon + tracker, the default), "
    "1 = drop the tracker channel (customiseTruthReduced), enough for the calorimeter "
    "associators and cheaper at DIGI")
options.parseArguments()

if options.gun != "closeBy" and options.delta != 10.0:
    raise ValueError(f"delta={options.delta} applies to the closeBy gun only (gun={options.gun}); "
                     "the flatEta gun shoots from the origin and has no particle separation")

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

# MC-truth graph (PhysicsTools/TruthInfo). The Phase2C26I13M9 era carries the enableTruth
# modifier, so the TruthGraphAccumulator already runs inside the mixing module (hence its
# warnings about absent sub-event hit collections) and the pileup-aware logical graph +
# hit index producers (truthLogicalGraphProducer, truthLogicalGraphHitIndexProducer) sit
# in pdigiTask, which runs them only when something keeps their products. The era adds
# the keeps to FEVTDEBUGHLT and RECOSIM, not to the FEVTDEBUG content written here, so
# they are added by hand: the compact truth (logical graph, unresolved hit index, raw
# merged graph TruthGraph_mix). RECO.py consumes these; it must never rebuild them.
from PhysicsTools.TruthInfo.truthEventContent_cff import setTruthEventContent
process = setTruthEventContent(process)
if options.truthReduced:
    from PhysicsTools.TruthInfo.mixedTruthGraphCustomize import customiseTruthReduced
    process = customiseTruthReduced(process)


# helper
def calculate_rho(z, eta):
    return z * math.tan(2 * math.atan(math.exp(-eta)))


if options.gun == "flatEta":
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
            MinE=cms.double(float(options.minE)),
            MaxE=cms.double(float(options.maxE)),
            # phi range
            MinPhi=cms.double(-math.pi),
            MaxPhi=cms.double(math.pi),
            # eta range
            MinEta=cms.double(1.5),
            MaxEta=cms.double(3.0),
        ),
        AddAntiParticle=cms.bool(False),
        debug=cms.untracked.bool(True),
        firstRun=cms.untracked.uint32(1)
    )
elif options.gun == "closeBy":
    # Same geometry as the earlier GNN energy-resolution samples
    # (metrics-data/{Gamma,Pion,Kaon}_Step3): the vertex is flat in R and z on
    # the +z HGCal front face and the momentum points away from the origin, so
    # the particle never crosses the tracker. These are the producer's own
    # defaults except NParticles (2 by default) and Delta (set from delta=, in cm).
    # With Overlapping=False particle i>0 is placed at the same R and z as the
    # first one, shifted in phi by Delta/R: the showers start Delta cm apart in
    # arc length, and Pointing=True makes them diverge like photons from the IP.
    process.generator = cms.EDProducer("CloseByParticleGunProducer",
        PGunParameters = cms.PSet(
            PartID=cms.vint32(int(options.particle)),
            NParticles=cms.int32(options.nParticles),
            RandomShoot=cms.bool(False),
            Overlapping=cms.bool(False),
            Delta=cms.double(float(options.delta)),
            # energy flat in [minE, maxE] (VarMin/VarMax are energies unless
            # FlatPtGeneration)
            VarMin=cms.double(float(options.minE)),
            VarMax=cms.double(float(options.maxE)),
            MaxVarSpread=cms.bool(False),
            FlatPtGeneration=cms.bool(False),
            # vertex position (cm), R-controlled rather than eta-controlled
            ControlledByEta=cms.bool(False),
            RMin=cms.double(60.),
            RMax=cms.double(120.),
            ZMin=cms.double(320.),
            ZMax=cms.double(321.),
            MinPhi=cms.double(-math.pi),
            MaxPhi=cms.double(math.pi),
            Pointing=cms.bool(True),
            UseDeltaT=cms.bool(False),
        ),
        AddAntiParticle=cms.bool(False),
        firstRun=cms.untracked.uint32(1)
    )
else:
    raise ValueError(f"unknown gun '{options.gun}' (expected flatEta or closeBy)")

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
