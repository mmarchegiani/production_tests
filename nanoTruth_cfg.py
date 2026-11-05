# Standalone nanoAOD config for the MC-truth-graph training truth.
#
# Reads a RECO.py output and writes a flat NanoAOD file with the TICL tracksters, the
# TICL candidates, the layer clusters, the GEN particles, the legacy SimTrackster truth
# with its reco<->sim association tables, and the calo-boundary truth of the MC-truth
# graph: the TruthCB particle table and the CLUEstering/Interpretations/LayerCluster <->
# TruthCB association tables (the layout of PhysicsTools/TruthInfo/python/truthNanoTables_cff.py).
#
# Unlike nanoML_cfg.py it needs nothing from the pepr branch (no DPGAnalysis pepr tables,
# no hgcSimTruth), so it runs in an area that carries only the TICLv6 + truth-graph
# packages. It writes no RecHit, SimCluster or MergedSimCluster tables; the parquet
# scripts treat those groups as optional. Table names follow nanoML_cfg.py so the parquet
# groups are the same (LayerCluster, ticlTrackstersCLUEstering, ticlSimTracksters, ...).
# The TICL blocks are copies of the nanoML_cfg.py ones: nanoML_cfg.py cannot be imported
# here, it loads the pepr content at import time.
#
# TICLv6 only. The input must be a RECO.py useTICL=1 file: first iteration
# ticlTrackstersCLUEstering (the CLUEstering algorithm on the device), final tracksters
# ticlTracksterInterpretations, ticlCandidate holding the candidates only. Every input file
# is checked for these two collections at configuration time and the job refuses to start
# otherwise: a TICLv5 file (ticlTrackstersCLUE3DHigh, ticlCandidate tracksters) is an error,
# never a fallback. The trackster tables and the reco <-> sim and reco <-> truth association
# tables are built for the first iteration, the links and the interpretations, with the
# short names CLUEstering, Links, Interpretations.

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from PhysicsTools.NanoAOD.common_cff import Var

process = cms.Process('NANO')
options = VarParsing('python')
options.setDefault('outputFile', 'testNanoTruth.root')
options.setDefault('maxEvents', -1)
options.register("nThreads", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "number of threads")
# Accepted for command-line compatibility with nanoML_cfg.py (chain_common.sh and
# run_stage.sh pass them); nothing here depends on them.
options.register("runPFTruth", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "ignored (nanoML_cfg.py compatibility)")
options.register("runMergedSimClusters", 0, VarParsing.multiplicity.singleton, VarParsing.varType.int,
    "ignored (nanoML_cfg.py compatibility)")
options.parseArguments()

# The TICLv6 trackster collections and their short names in the association tables.
_ticlFirstIteration = "ticlTrackstersCLUEstering"
_ticlFinalTracksters = "ticlTracksterInterpretations"
_ticlIterLabels = ["ticlTrackstersCLUEstering", "ticlTrackstersRecovery", "ticlTracksterLinks",
                   "ticlTracksterLinksSuperclusteringDNN", "ticlTracksterInterpretations"]
_ticlShortNames = {"ticlTrackstersCLUEstering": "CLUEstering", "ticlTracksterLinks": "Links",
                   "ticlTracksterInterpretations": "Interpretations"}


def _requireTICLv6Input(fileNames):
    """Refuse to configure unless every input file carries the TICLv6 tracksters: the branches
    ticlTracksters_<label>_*_* of the first iteration and of the interpretation stage. A file
    without them (a TICLv5 file, for one) would give empty tables instead of an error at run
    time, since the tables skip a missing source, so the check is made here and raises."""
    if not fileNames:
        raise RuntimeError("nanoTruth_cfg.py: no input file given (inputFiles=...), "
                           "so the TICLv6 tracksters cannot be checked")
    import ROOT
    for fileName in fileNames:
        path = str(fileName)
        if path.startswith("file:"):
            path = path[len("file:"):]
        try:
            inputFile = ROOT.TFile.Open(path)
        except OSError:  # recent ROOT raises instead of returning a null file
            inputFile = None
        if not inputFile or inputFile.IsZombie():
            raise RuntimeError(f"nanoTruth_cfg.py: cannot open the input file {fileName} "
                               "to check for the TICLv6 tracksters")
        events = inputFile.Get("Events")
        branches = [branch.GetName() for branch in events.GetListOfBranches()] if events else []
        inputFile.Close()
        missing = [label for label in (_ticlFirstIteration, _ticlFinalTracksters)
                   if not any(name.startswith(f"ticlTracksters_{label}_") for name in branches)]
        if missing:
            raise RuntimeError(
                f"nanoTruth_cfg.py: the input file {fileName} is not a TICLv6 RECO file: it has no "
                + " and no ".join(missing)
                + " tracksters. This config reads TICLv6 only; produce the input with RECO.py useTICL=1")


_requireTICLv6Input(options.inputFiles)

process.load('Configuration.StandardSequences.Services_cff')
process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
process.load('FWCore.MessageService.MessageLogger_cfi')
process.load('Configuration.EventContent.EventContent_cff')
process.load('Configuration.Geometry.GeometryExtendedRun4D128Reco_cff')
process.load('Configuration.Geometry.GeometryExtendedRun4D128_cff')
process.load('Configuration.StandardSequences.MagneticField_cff')
process.load('Configuration.StandardSequences.EndOfProcess_cff')
process.load('Configuration.StandardSequences.FrontierConditions_GlobalTag_cff')
# The candidate extra table propagates the candidates' tracks to the HGCAL surfaces.
process.load("TrackingTools.MaterialEffects.MaterialPropagator_cfi")
process.load("TrackingTools.MaterialEffects.OppositeMaterialPropagator_cfi")

from Configuration.AlCa.GlobalTag import GlobalTag
process.GlobalTag = GlobalTag(process.GlobalTag, 'auto:mc', '')

process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(options.maxEvents))
process.MessageLogger.cerr.FwkReport.reportEvery = 100

process.source = cms.Source("PoolSource",
    fileNames=cms.untracked.vstring(options.inputFiles),
    secondaryFileNames=cms.untracked.vstring())

process.options = cms.untracked.PSet(
    numberOfThreads=cms.untracked.uint32(options.nThreads),
    numberOfStreams=cms.untracked.uint32(0),
    wantSummary=cms.untracked.bool(False),
    # A table whose input is missing is written empty instead of aborting the job.
    TryToContinue=cms.untracked.vstring('ProductNotFound'),
)

process.NANOAODSIMoutput = cms.OutputModule("NanoAODOutputModule",
    compressionAlgorithm=cms.untracked.string('LZMA'),
    compressionLevel=cms.untracked.int32(9),
    dataset=cms.untracked.PSet(dataTier=cms.untracked.string('NANOAODSIM'), filterName=cms.untracked.string('')),
    fileName=cms.untracked.string(options.__getattr__("outputFile", noTags=True)),
    outputCommands=process.NANOAODSIMEventContent.outputCommands)
process.NANOAODSIMoutput.outputCommands.remove("keep edmTriggerResults_*_*_*")
process.NANOAODSIMoutput.outputCommands.append("keep nanoaodFlatTable_*_*_*")

from PhysicsTools.NanoAOD.nano_cff import nanoMetadata
process.nanoMetadata = nanoMetadata

# ---------------------------------------------------------------------------
# GEN particles (upstream DPGAnalysis/HGCalNanoAOD table, name HGCalGenPart)
# ---------------------------------------------------------------------------
from DPGAnalysis.HGCalNanoAOD.hgcalGen_cfi import hgcalGenPartTable
process.hgcalGenPartTable = hgcalGenPartTable

# ---------------------------------------------------------------------------
# Layer clusters: the upstream producer with the pepr column names, so the
# parquet LayerCluster group (x, y, z, eta, phi, energy, nHits, seedDetId) is the
# same as in nanoML files. The CaloPart/SimCluster match columns of nanoML are not
# written here; the truth of a layer cluster is LayerClusterToTruthCB below.
# ---------------------------------------------------------------------------
process.layerClusterTable = cms.EDProducer("LayerClustersCollectionTableProducer",
    skipNonExistingSrc=cms.bool(True),
    src=cms.InputTag("hgcalMergeLayerClusters"),
    cut=cms.string(""),
    name=cms.string("LayerCluster"),
    doc=cms.string("hgcalMergeLayerClusters"),
    singleton=cms.bool(False),
    variables=cms.PSet(
        eta=Var("eta", "float", doc="eta"),
        phi=Var("phi", "float", doc="phi"),
        energy=Var("energy", "float", doc="Energy [GeV]"),
        x=Var("x", "float", doc="x [cm]"),
        y=Var("y", "float", doc="y [cm]"),
        z=Var("z", "float", doc="z [cm]"),
        nHits=Var("size", "int", doc="Number of rechits"),
        seedDetId=Var("seed().rawId()", "int", doc="DetId of the seed rechit"),
        algoID=Var("algoID", "int", doc="Clustering algorithm"),
    ),
)
process.layerClusterExtraTable = cms.EDProducer("LayerClustersExtraTableProducer",
    tableName=cms.string("LayerCluster"),
    skipNonExistingSrc=cms.bool(True),
    time_layerclusters=cms.InputTag("hgcalMergeLayerClusters", "timeLayerCluster"),
    precision=cms.int32(-1),
)

# ---------------------------------------------------------------------------
# TICL candidates and tracksters (copied from nanoML_cfg.py)
# ---------------------------------------------------------------------------
process.ticlCandidateTable = cms.EDProducer("TICLCandidateTableProducer",
    skipNonExistingSrc=cms.bool(True),
    src=cms.InputTag("ticlCandidate"),
    cut=cms.string(""),
    name=cms.string("TICLCand"),
    doc=cms.string("Offline TICLCandidates (from ticlCandidate producer)"),
    singleton=cms.bool(False),
    variables=cms.PSet(
        raw_energy=Var("rawEnergy", "float", doc="Raw energy [GeV]"),
        pt=Var("pt", "float", doc="pT [GeV]"),
        p=Var("p", "float", doc="|p| [GeV]"),
        energy=Var("energy", "float", doc="Energy [GeV]"),
        eta=Var("eta", "float", doc="eta"),
        phi=Var("phi", "float", doc="phi"),
        mass=Var("mass", "float", doc="mass"),
        pdgID=Var("pdgId", "int", doc="pdgId"),
        charge=Var("charge", "int", doc="charge"),
        time=Var("time", "float", doc="HGCAL time"),
        timeError=Var("timeError", "float", doc="HGCAL time error"),
    ),
)
# The tracksters the candidates point to: the final tracksters of the interpretation stage
# (Candidate2TrackstersIndices index that table).
_ticlCandidateTracksters = _ticlFinalTracksters
process.ticlCandidateExtraTable = cms.EDProducer("TICLCandidateExtraTableProducer",
    src=cms.InputTag("ticlCandidate"),
    tracksters=cms.InputTag(_ticlCandidateTracksters),
    tracks=cms.InputTag("generalTracks"),
    name=cms.string("TICLCand"),
    skipNonExistingSrc=cms.bool(True),
    doc=cms.string("TICLCandidates extra table with linked Tracksters"),
    collectionVariables=cms.PSet(
        tracksters=cms.PSet(
            name=cms.string("Candidate2TrackstersIndices"),
            doc=cms.string("Tracksters linked to TICLCandidates (index into the %s table)" % _ticlCandidateTracksters),
            useCount=cms.bool(True),
            useOffset=cms.bool(False),
            variables=cms.PSet(),
        ),
    ),
)

_tracksterVariables = cms.PSet(
    raw_energy=Var("raw_energy", "float", doc="Raw energy [GeV]"),
    raw_em_energy=Var("raw_em_energy", "float", doc="EM raw energy [GeV]"),
    raw_pt=Var("raw_pt", "float", doc="Raw pT [GeV]"),
    regressed_energy=Var("regressed_energy", "float", doc="Regressed energy"),
    barycenter_x=Var("barycenter.x", "float", doc="Barycenter x [cm]"),
    barycenter_y=Var("barycenter.y", "float", doc="Barycenter y [cm]"),
    barycenter_z=Var("barycenter.z", "float", doc="Barycenter z [cm]"),
    barycenter_eta=Var("barycenter.eta", "float", doc="Barycenter eta"),
    barycenter_phi=Var("barycenter.phi", "float", doc="Barycenter phi"),
    EV1=Var("eigenvalues()[0]", "float", doc="Trackster PCA eigenvalues 0"),
    EV2=Var("eigenvalues()[1]", "float", doc="Trackster PCA eigenvalues 1"),
    EV3=Var("eigenvalues()[2]", "float", doc="Trackster PCA eigenvalues 2"),
    eVector0_x=Var("eigenvectors()[0].x", "float", doc="Trackster PCA principal axis, x component"),
    eVector0_y=Var("eigenvectors()[0].z", "float", doc="Trackster PCA principal axis, y component"),
    eVector0_z=Var("eigenvectors()[0].y", "float", doc="Trackster PCA principal axis, z component"),
    time=Var("time", "float", doc="HGCAL time"),
    timeError=Var("timeError", "float", doc="HGCAL time error"),
)

def _tracksterTable(name, src, doc):
    return cms.EDProducer("TracksterCollectionTableProducer",
        skipNonExistingSrc=cms.bool(True),
        src=src,
        cut=cms.string(""),
        name=cms.string(name),
        doc=cms.string(doc),
        singleton=cms.bool(False),
        variables=_tracksterVariables.clone(),
        collectionVariables=cms.PSet(
            tracksterVertices=cms.PSet(
                name=cms.string(name + "vertices"),
                doc=cms.string("Trackster<->LayerCluster association (vertex = LC index)"),
                useCount=cms.bool(True),
                useOffset=cms.bool(True),
                variables=cms.PSet(
                    vertices=Var("vertices", "uint", doc="LayerCluster index"),
                    vertex_mult=Var("vertex_multiplicity", "float", doc="Fraction of LC energy used by the trackster"),
                ),
            ),
        ),
    )

_ticlTableProducers = []
for _iterLabel in _ticlIterLabels:
    setattr(process, _iterLabel + "Table", _tracksterTable(_iterLabel, cms.InputTag(_iterLabel), _iterLabel))
    _ticlTableProducers.append(getattr(process, _iterLabel + "Table"))

# ---------------------------------------------------------------------------
# Legacy MC truth: SimTracksters (seeded from SimClusters/CaloParticles) and the
# reco<->sim trackster association tables, for cross-checks of the graph truth
# (copied from nanoML_cfg.py; see there for the column semantics).
# ---------------------------------------------------------------------------
_simTracksterCollections = {
    "ticlSimTracksters": cms.InputTag("ticlSimTracksters"),
    "ticlSimTrackstersFromCPs": cms.InputTag("ticlSimTracksters", "fromCPs"),
}
_simTracksterTableProducers = []
for _name, _src in _simTracksterCollections.items():
    _prod = _tracksterTable(_name, _src, _name)
    _prod.variables.boundaryTime = Var("boundaryTime", "float", doc="Seed time at the calo boundary [ns]")
    _prod.variables.seedIndex = Var("seedIndex", "int", doc="Index of the seed: SimCluster table row or CaloPart table row, see seedProductId")
    _prod.variables.seedProductId = Var("seedID().id()", "uint", doc="ProductID of the seed collection")
    _prod.collectionVariables.tracksterVertices.doc = cms.string("SimTrackster<->LayerCluster association (vertex = LC index)")
    setattr(process, _name + "Table", _prod)
    _simTracksterTableProducers.append(getattr(process, _name + "Table"))
    _extra = cms.EDProducer("SimTracksterTableProducer",
        tableName=cms.string(_name),
        skipNonExistingSrc=cms.bool(True),
        simTracksters=_src,
        caloParticles=cms.InputTag("mix", "MergedCaloTruth"),
        simClusters=cms.InputTag("mix", "MergedCaloTruth"),
        caloParticleToSimClustersMap=cms.InputTag("ticlSimTracksters"),
        precision=cms.int32(-1),
    )
    setattr(process, _name + "ExtraTable", _extra)
    _simTracksterTableProducers.append(getattr(process, _name + "ExtraTable"))

process.ticlSimCandidateTable = process.ticlCandidateTable.clone(
    src=cms.InputTag("ticlSimTracksters"),
    name=cms.string("SimTICLCand"),
    doc=cms.string("Sim TICLCandidates from ticlSimTracksters; rows parallel to ticlSimTrackstersFromCPs"),
)
process.ticlSimCandidateExtraTable = process.ticlCandidateExtraTable.clone(
    src=cms.InputTag("ticlSimTracksters"),
    tracksters=cms.InputTag("ticlSimTracksters"),
    name=cms.string("SimTICLCand"),
    doc=cms.string("Sim TICLCandidates extra table with linked SimTracksters"),
    collectionVariables=cms.PSet(
        tracksters=cms.PSet(
            name=cms.string("SimCandidate2TrackstersIndices"),
            doc=cms.string("ticlSimTracksters indices linked to each SimTICLCand"),
            useCount=cms.bool(True),
            useOffset=cms.bool(True),
            variables=cms.PSet(),
        ),
    ),
)

# CLUEstering, Links and Interpretations.
_simAssocRecoLabels = {_label: _ticlShortNames[_label] for _label in _ticlIterLabels if _label in _ticlShortNames}
_simAssocSimLabels = {"ticlSimTracksters": "SimTS", "ticlSimTrackstersfromCPs": "SimTSCP"}
_simAssocTypes = ["ByHits", "ByLCs"]

def _simAssocTable(src, name, doc, indexDoc):
    return cms.EDProducer("TracksterTracksterEnergyScoreFlatTableProducer",
        src=src,
        skipNonExistingSrc=cms.bool(True),
        name=cms.string(name),
        doc=cms.string(doc),
        collectionVariables=cms.PSet(
            links=cms.PSet(
                name=cms.string(name + "Links"),
                doc=cms.string("Association links"),
                useCount=cms.bool(True),
                useOffset=cms.bool(True),
                variables=cms.PSet(
                    index=Var("index", "uint", doc=indexDoc),
                    sharedEnergy=Var("sharedEnergy", "float", doc="Shared energy with the linked trackster [GeV]"),
                    score=Var("score", "float", doc="Association score (lower is better)"),
                ),
            ),
        ),
    )

_simAssocTableProducers = []
for _recoLabel, _recoShort in _simAssocRecoLabels.items():
    for _simLabel, _simShort in _simAssocSimLabels.items():
        for _type in _simAssocTypes:
            _assocModule = "allTrackstersToSimTrackstersAssociations" + _type
            _r2s = _recoShort + "To" + _simShort + _type
            setattr(process, "ticlAssoc" + _r2s + "Table", _simAssocTable(
                cms.InputTag(_assocModule, _recoLabel + "To" + _simLabel), _r2s,
                f"{_recoLabel} -> {_simLabel} association ({_type})", f"Index into the {_simLabel} table"))
            _simAssocTableProducers.append(getattr(process, "ticlAssoc" + _r2s + "Table"))
            _s2r = _simShort + "To" + _recoShort + _type
            setattr(process, "ticlAssoc" + _s2r + "Table", _simAssocTable(
                cms.InputTag(_assocModule, _simLabel + "To" + _recoLabel), _s2r,
                f"{_simLabel} -> {_recoLabel} association ({_type})", f"Index into the {_recoLabel} table"))
            _simAssocTableProducers.append(getattr(process, "ticlAssoc" + _s2r + "Table"))

# ---------------------------------------------------------------------------
# MC-truth graph: TruthCB particle table and the reco <-> TruthCB association tables
# ---------------------------------------------------------------------------
process.load("PhysicsTools.TruthInfo.truthNanoTables_cff")

# The reco -> truth maps at each working point: Fixed keeps every match, best first; the
# adaptive points keep one match per reco object. Plus the truth -> reco map. Each <A> table
# below is a count/offset table with its <A>Links table.
#   calo-boundary (RECO.py truthAssoc=1): links index TruthCB, candidates are boundary
#     particles only, so an adaptive point picks the best one and cannot climb.
#   full graph (RECO.py truthFullGraph=1): links index TruthFG, the particles a reco object
#     may be assigned to (truthBranchTargets "assignableRoots") with HGCAL sim energy, so
#     an adaptive point can answer with an ancestor of the boundary particles.
# A RECO file without the full-graph maps gets empty TruthFG tables (skipNonExistingSrc).
# truthNanoTables_cff writes the Fixed and TruthToReco calo-boundary tables for
# ticlTrackstersCLUE3DHigh and the layer clusters; they are generated here instead, for the
# trackster collections of this input, so only its TruthCB particle table is scheduled.
from PhysicsTools.TruthInfo.truthParticleFlatTable_cfi import truthParticleFlatTable
from PhysicsTools.TruthInfo.truthBranchAssociationFlatTable_cfi import truthBranchAssociationFlatTable

_adaptiveWorkingPoints = ["AdaptiveTight", "AdaptiveNominal"]
# reco short name -> (associator module suffix, reco collection label): the first TICL
# iteration, the final tracksters and the layer clusters (the RECO.py calo-boundary
# associators cover exactly these; the full-graph ones cover every trackster collection).
_truthAssocRecos = {
    _ticlShortNames[_ticlFirstIteration]: ("TracksterAssociators", _ticlFirstIteration),
    _ticlShortNames[_ticlFinalTracksters]: ("TracksterAssociators", _ticlFinalTracksters),
    "LayerCluster": ("LayerClusterAssociators", "hgcalMergeLayerClusters"),
}

process.ticlTruthFGTable = truthParticleFlatTable.clone(
    targets=cms.InputTag("truthBranchTargets", "assignableRoots"),
    requireHGCALHits=True,
    name="TruthFG",
    doc="MC-truth graph particles a reco object may be assigned to (full graph, signal and pile-up) "
        "with HGCAL sim energy; boundary columns are zero when hasBoundary is false",
)

# truth short name -> (associator module prefix, particle table, what an adaptive point answers)
_truthLevels = {
    "TruthCB": ("truthCaloBoundary", "ticlTruthCBTable", "the best calo-boundary particle"),
    "TruthFG": ("truthBranch", "ticlTruthFGTable", "one particle, the adaptive climb may answer with an ancestor"),
}
_truthAssocTableProducers = [process.ticlTruthFGTable]
for _recoShort, (_assocSuffix, _recoLabel) in _truthAssocRecos.items():
    for _truthShort, (_assocPrefix, _targetsTable, _adaptiveDoc) in _truthLevels.items():
        # reco -> truth: Fixed and the adaptive points
        for _wp in ["Fixed"] + _adaptiveWorkingPoints:
            _name = f"{_recoShort}To{_truthShort}" + ("" if _wp == "Fixed" else _wp)
            setattr(process, f"ticlTruthAssoc{_name}Table", truthBranchAssociationFlatTable.clone(
                src=cms.InputTag(f"{_assocPrefix}{_assocSuffix}", f"{_recoLabel}RecoToTruth{_wp}"),
                targets=cms.InputTag(_targetsTable, "selected"),
                direction="recoToTruth",
                name=_name,
                doc=f"{_recoLabel} -> {_truthShort}, {_wp} working point: "
                    + ("every particle sharing energy, best first" if _wp == "Fixed" else _adaptiveDoc),
            ))
            _truthAssocTableProducers.append(getattr(process, f"ticlTruthAssoc{_name}Table"))
        # truth -> reco
        _name = f"{_truthShort}To{_recoShort}"
        setattr(process, f"ticlTruthAssoc{_name}Table", truthBranchAssociationFlatTable.clone(
            src=cms.InputTag(f"{_assocPrefix}{_assocSuffix}", f"{_recoLabel}TruthToReco"),
            targets=cms.InputTag(_targetsTable, "selected"),
            direction="truthToReco",
            name=_name,
            doc=f"{_truthShort} -> {_recoLabel}: every reco object sharing energy, best first",
        ))
        _truthAssocTableProducers.append(getattr(process, f"ticlTruthAssoc{_name}Table"))

process.nanoTruthTask = cms.Task(
    process.nanoMetadata,
    process.hgcalGenPartTable,
    process.layerClusterTable,
    process.layerClusterExtraTable,
    process.ticlCandidateTable,
    process.ticlCandidateExtraTable,
    *_ticlTableProducers,
    *_simTracksterTableProducers,
    process.ticlSimCandidateTable,
    process.ticlSimCandidateExtraTable,
    *_simAssocTableProducers,
    process.ticlTruthCBTable,
    *_truthAssocTableProducers,
)
process.nanoAOD_step = cms.Path(process.nanoTruthTask)
process.endjob_step = cms.EndPath(process.endOfProcess)
process.NANOAODSIMoutput_step = cms.EndPath(process.NANOAODSIMoutput)
process.schedule = cms.Schedule(process.nanoAOD_step, process.endjob_step, process.NANOAODSIMoutput_step)
