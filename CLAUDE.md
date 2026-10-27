# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a HGCal (High-Granularity Calorimeter) NanoML production test suite for the CMS experiment at CERN. It produces flat nanoAOD ntuples from simulated Phase-2 detector data for machine learning applications in HGCal reconstruction. It is a fork of [kdlong/production_tests](https://github.com/kdlong/production_tests), based on [CMS-HGCAL/reco-prodtools](https://github.com/CMS-HGCAL/reco-prodtools).

**This is the `pre4_D128` branch**: `CMSSW_20_1_0_pre4` with the `Run4D128` geometry, where TICL is the default (and only) reconstruction path. The `CMSSW_20_0_0_pre1` / `Run4D121` version lives on the `pre1_D121` branch. The `CMSSW_15_1_0` / `Run4D110` version (with an opt-in `useTICL` flag and separate `nanoTICL_cfg.py`/`nanoTICL-ML_cfg.py` configs) lives on the `master` branch — those two configs do not exist on this branch.

## Environment Setup

```shell
export SCRAM_ARCH=el9_amd64_gcc14
version=CMSSW_20_1_0_pre4
cmsrel $version && cd $version/src && cmsenv
git cms-init -y
git cms-merge-topic dgaytanv:pepr_${version}
git cms-checkdeps -a   # pepr changes the SimCluster layout: rebuild every package that uses it
scram b -j 4      # -j 12 can OOM on cmslpc

# Generate RECO fragment templates (D128 geometry)
git clone git@github.com:dgaytanv/reco-prodtools.git reco_prodtools
cd reco_prodtools/templates/python && ./produceSkeletons_D128.sh
cd ../../.. && scram b

git clone -b pre4_D128 git@github.com:dgaytanv/production_tests.git && cd production_tests
```

## Running the Production Chain

Each step is a standalone `cmsRun` invocation. All configs use `VarParsing` for runtime options.

**Step 1 (optional) — MINBIAS (only needed for pileup mixing):**
```shell
cmsRun MINBIAS_GENSIM.py seed=42 maxEvents=200 outputFile=pileup_gensim.root
# Pythia8 SoftQCD (non-diff + single + double diff), 14 TeV, CP5 tune.
# Use a seed distinct from the signal GSD run; nEvents should be >= average PU.
```

**Step 2 — GSD (Generate → Sim → Digi):**
```shell
cmsRun GSD_GUN.py seed=1 outputFile=testGSD.root
# Options: nParticles=1, useFineCalo=1, particle=22, energy=100.0, nThreads=1, maxEvents=10
# Pileup: pileup=N (average PU, default 0) requires pu=<minbias.root> from step 1
```

**Step 3 — RECO (Reconstruction):**
```shell
cmsRun RECO.py inputFiles=file:testGSD.root outputFile=testRECO.root
# useTICL is accepted for CLI compatibility with the master (D110) branch but is a
# no-op here — TICL v5 is always on and always in the output (ticlTrackstersCLUE3DHigh,
# ticlTracksterLinks, ticlTracksterLinksSuperclusteringDNN, ticlTracksterRecovery, ticlCandidate).
```

**Step 4 — nanoML ntuple production:**
```shell
cmsRun nanoML_cfg.py inputFiles=file:testRECO.root outputFile=testNanoML.root
# runPFTruth={0,1} — PFTruth sequence, currently broken with pileup (default 0)
# runMergedSimClusters={0,1} — pepr SimClusterMerger (hgcSimTruth) + MergedSimCluster tables
#   (default 1); MUST be 0 with pileup: the merger walks the signal-only g4SimHits
#   SimTrack/SimVertex history and throws vector::_M_range_check on the first pileup
#   SimCluster. chain_common.sh / condor/run_stage.sh set it from the pileup value.
```
`nanoML_cfg.py` is now the only nano config on this branch — it produces a single flat
NanoAOD file (~600 branches) that already includes the TICL trackster/candidate tables,
the SimTrackster (MC-truth trackster) tables and the reco↔sim trackster association
tables; there is no separate `_ticl.root` output.

**Step 5 (optional) — flatten to parquet for ML training:**
```shell
python3 root_to_parquet.py --nanoMLfiles testNanoML.root --outputDir parquet_out --outputFile out.parquet
# or root_to_parquet-split.py to split each event into one row per HGCAL endcap
# (negative endcap mirrored z -> -z, eta -> -eta so both read as +z)
```
The CMSSW python has uproot/awkward but no `pyarrow`, so this step needs another python.
`scripts/chain_common.sh` runs it via `apptainer exec --cleanenv` in the
`hgcal-offline-reco:3.12-latest` image (`/cvmfs/unpacked.cern.ch/gitlab-registry.cern.ch/cms-cmu/hgcal-ml4reco/`,
override with `PARQUET_IMAGE`, or set `PARQUET_PYTHON` to skip the container).

**One-shot local chain:** the `scripts/generate_*.sh` wrappers (and the generic `scripts/generate.sh --particle <pdgId>`)
source `scripts/chain_common.sh` and run `[MINBIAS →] GSD → RECO → nanoML → parquet` on one machine; they preset
`PARTICLE`/`PARTNAME`/`USE_FINE_CALO` and optionally `PILEUP`. `scripts/generate_taus_noFineCalo_30PU.sh` presets
`PILEUP=30` (minbias library of `n_events` events generated with seed+500000, `--nminbias`/`--pu` to change that,
files tagged `taus_stdCalo_PU30_*`); only the `noFineCalo` configuration supports pileup.

`root_to_parquet-split.py` also accepts an XRootD URL as `--outputDir` (e.g.
`root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets/<dataset>`):
the parquet file is then streamed to the remote storage one row group at a time, written as
`<name>.parquet.part` and renamed on success (the partial file is removed on any error; a job
killed by SIGKILL leaves the `.part` behind). It refuses to start if the remote file exists
(`--overwrite` to replace it). This needs the XRootD python bindings, which the apptainer image
lacks: use an LCG view (`source /cvmfs/sft.cern.ch/lcg/views/LCG_109/x86_64-el9-gcc13-opt/setup.sh`)
with a valid VOMS proxy. Without the bindings but with `xrdcp` in PATH it falls back to a local
staging file in `--stage-dir` (default `$TMPDIR`/`/tmp`) copied at the end. Remote writes are
buffered in 32 MB requests: pyarrow emits one small write per column page and each XRootD write is
a synchronous round trip, which unbuffered cost ~2 min per row group to CMU.

**Parallel conversion, one parquet file per ROOT file (preferred for campaigns):**
```shell
condor/submit_parquet.sh --indir /eos/user/.../nanoML \
    --outdir root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets/<dataset> \
    --workdir ~/condor/<campaign>/parquet      # run from a shell WITHOUT cmsenv
```
Builds and submits a DAG with one `condor/run_parquet.sh stage=convert` job per nanoML file (input fetched
into the sandbox with xrdcp, output `<base>_split.parquet` streamed to `--outdir`, ~2 min and 4.3 GB per
500-event file on 2 cores; jobs whose output already exists exit immediately, so resubmitting the DAG only
fills the gaps, `--force` overwrites) and a final `stage=metadata` node that runs
`parquet_folder_metadata.py <folder>`. That script writes `_metadata`/`_common_metadata` so the folder reads as
one dataset (`ak.from_parquet(folder)`, `pq.ParquetDataset(folder)`; also works on a `root://` folder with
fsspec-xrootd) and checks that every file has the same schema, since row groups can only be merged across
identical schemas (`--check-only`, `--skip-bad`). The VOMS proxy is copied to `<workdir>/proxy/` with an
owner-only AFS ACL because DAGMan submits the node jobs from the schedd, where `/tmp` of lxplus is not visible.
With `--merge` the script takes several input directories (repeatable `--indir`, or an expanded glob as positional
arguments) and runs one job per directory that merges all its ROOT files into a single parquet file in
`--outdir/<dataset>/`, `<dataset>` being the input directory name (its parent when the directory is `nanoML`) and
the file named after the common prefix before `_rep<N>` (e.g. `photons_noFineCalo_with_tracksters_10GeV_1k/photons_stdCalo_E10_split.parquet`),
each folder getting its own metadata node — this keeps the lxplus naming scheme on the remote side for small
evaluation samples. The job wrapper takes `infiles=a,b,c` (comma-separated, fetched one by one with xrdcp).

## Architecture

The configs follow a **composition pattern**: each file imports a base `process` object from `reco_prodtools` templates, then appends producers, modifies output commands, and adjusts the schedule.

### MINBIAS_GENSIM.py
Imports the same `GSD_fragment`/`GSDfineCalo_fragment` base as `GSD_GUN.py` (for geometry/era/conditions/output plumbing) but swaps the generator for a `Pythia8GeneratorFilter` running SoftQCD. Its output is a GEN-SIM library consumed by `GSD_GUN.py` via `pu=<file>`; it is a standalone producer, not itself pileup-mixed.

### GSD_GUN.py
Selects between three base fragments: `GSDfineCalo_fragment` (`useFineCalo=1`, no PU support), `GSD_fragment_PU` (`pileup > 0`, a cmsDriver-generated fragment with `--pileup AVE_30_BX_25ns` baked in — the placeholder DAS input is swapped for the local `pu=` minbias file), or plain `GSD_fragment` otherwise. Configures pepr's `edm::FlatEtaRangeGunProducer` (default: photon; `particle=`, `minE=`/`maxE=` — the `energy=` option is registered but unused, `condor/run_stage.sh` always passes `minE`/`maxE`). Per event it shoots `nParticles` particles into the +z endcap AND `nParticles` into −z, each with independent flat η ∈ [1.5, 3.0], φ ∈ [−π, π] and energy flat in E (not pT) between `minE` and `maxE`, from the origin: with the default `nParticles=1` every event has **two showers, one per endcap**.

### RECO.py
Always imports `RECO_fragment` (TICL v5 is the default in pre1; the `ticl_v5`-suffixed fragment no longer exists, and `useTICL` is kept only for CLI compatibility). Explicitly instantiates and schedules truth-matching association producers (`lcAssocByEnergyScoreProducer`, `layerClusterCaloParticleAssociationProducer`, `scAssocByEnergyScoreProducer`, `layerClusterSimClusterAssociationProducer`, `simHitTPAssocProducer`, `trackingParticleRecoTrackAsssociation`) as a `cms.Task` added to `recosim_step`. Also schedules the MC-truth tracksters and their associators, which in CMSSW live only in the validation sequence: `filteredLayerClustersSimTracksters` + `ticlSimTracksters` (products under the single module label `ticlSimTracksters`: SimCluster/CaloParticle-seeded tracksters in the default instance, `fromCPs`, `PU`, sim `TICLCandidate`s, and a CaloParticle→SimTrackster index map), `allLayerClusterToTracksterAssociations`, `allHitToTracksterAssociations`, `hitToSimClusterCaloParticleAssociator`, `allTrackstersToSimTrackstersAssociationsByLCs`/`ByHits` (reco↔sim trackster scores, instance labels like `ticlTrackstersCLUE3DHighToticlSimTracksters`), and `SimClusterToCaloParticleAssociation`. Each SimTrackster carries `seedID()`/`seedIndex()` pointing at the SimCluster or CaloParticle it was built from (`mix:MergedCaloTruth`). Note the LC→SimCluster / LC→CaloParticle associator module labels must match their producer types, since `ticlSimTracksters` consumes them by label with distinct C++ types. Also runs pepr's `SimClusterMerger` (mergedSimClusters). The `outputCommands` list is carefully curated to retain all objects needed by `nanoML_cfg.py`.

### nanoML_cfg.py
Builds a fresh `cms.Process('NANO')` (no imported base process; geometry loaded directly as `GeometryExtendedRun4D128`/`GeometryExtendedRun4D128Reco`). Loads `DPGAnalysis.HGCalNanoAOD.nanoHGCML_cff` and applies `customizeMergedSimClusters()` and `customizeReco()`. Disables `pfTruth`/`trackSCAssocTable` sequences when `runPFTruth=0` (needed with pileup), and removes the whole `mergedSimClusterTables` sequence (`hgcSimTruth`, `closeBySimClusters`, `MergedSimCluster` table + extensions, `RecHitHGC_MergedSimClusterBestMatch*`) when `runMergedSimClusters=0` (needed with pileup: pepr's `SimClusterMerger` builds its `SimHistoryTool` from the signal-only `g4SimHits` SimTracks/SimVertices and the first pileup SimCluster indexes past them — `vector::_M_range_check: __n (which is 1027) >= this->size() (which is 65)`). Pileup samples therefore have `SimCluster` and the pileup-aware `MergedCaloTruthMergedSimCluster` truth but no `MergedSimCluster`; `root_to_parquet*.py` skip an absent branch group. Removes the `iso` field from `genParticleTable` to avoid missing-product errors. Adds bespoke TICL nano tables not covered by the default event content:
- `ticlCandidateTable` (`TICLCandidateTableProducer`) — kinematics for `ticlCandidate` → `TICLCand`.
- `ticlCandidateExtraTable` (`TICLCandidateExtraTableProducer`) — `Candidate2Tracksters` index table linking TICLCandidates to their tracksters.
- One `TracksterCollectionTableProducer` per TICL iteration (`ticlTrackstersCLUE3DHigh`, `ticlTracksterLinks`, `ticlTracksterRecovery`, `ticlTracksterLinksSuperclusteringDNN`), each carrying a `tracksterVertices` collection variable that gives the Trackster↔LayerCluster link.
- SimTrackster tables `ticlSimTracksters` (one per SimCluster, or per CaloParticle when its G4 track crossed the calo boundary) and `ticlSimTrackstersFromCPs` (one per CaloParticle), same kinematics + `vertices` as the reco tables plus `seedIndex` (row in the `SimCluster` or `CaloPart` table) and `seedProductId` (ProductID of the seed collection: a `ticlSimTracksters` entry is CaloParticle-seeded iff it equals the fromCPs value — `root_to_parquet*.py` derive `seedIsCaloParticle` from this). A `SimTracksterTableProducer` extension table adds the seed's boundary position/momentum, `simTime`, `genPt`, `mass` (upstream bug: `boundaryPhi` holds the eta value; `simEnergy` is computed but not written).
- `SimTICLCand` / `SimCandidate2Tracksters`: the sim TICLCandidates (rows parallel to `ticlSimTrackstersFromCPs`; `tracksterIndex` points into `ticlSimTracksters`).
- Reco↔sim association tables from `allTrackstersToSimTrackstersAssociationsBy{Hits,LCs}` via `TracksterTracksterEnergyScoreFlatTableProducer`, one per (reco ∈ {CLUE3DHigh, Links}) × (sim ∈ {SimTS, SimTSCP}) × direction × type, e.g. `CLUE3DHighToSimTSByHits` (rows parallel to `ticlTrackstersCLUE3DHigh`) with a flat `CLUE3DHighToSimTSByHitsLinks` table (`index`, `sharedEnergy`, `score`; lower score = better). Extend `_simAssocRecoLabels` to add iterations — only those in `ticlIterLabelsPSet` exist upstream.
These are scheduled as `ticlTablesTask`, attached to `nanoAOD_step` alongside `nanoHGCMLSequence`; every module label starts with `ticl` so the `keep nanoaodFlatTable_ticl*_*_*` output command picks them up. The plugins backing these producers live in `HLTrigger/NGTScouting` (originally written for HLT scouting) but are wired here with offline input tags.

### root_to_parquet.py / root_to_parquet-split.py
Flatten nanoML ROOT files into parquet for ML training, grouping branches by object family (`RecHitHGC`, `LayerCluster`, `SimCluster`, `MergedSimCluster`, `MergedCaloTruthMergedSimCluster`, `TICLCand`, `Candidate2Tracksters`, per-iteration tracksters, the SimTrackster groups `ticlSimTracksters`/`ticlSimTrackstersFromCPs`, `SimTICLCand`/`SimCandidate2Tracksters`, and the reco↔sim association tables generated from `SIM_ASSOC_RECO`/`SIM_ASSOC_SIM`/`SIM_ASSOC_TYPES`). Both compute `SimCluster_isPileup`/`MergedSimCluster_isPileup` as `~((bunchCrossing == 0) & (eventId == 0))` and `<simgroup>_seedIsCaloParticle` (seedProductId equal to the fromCPs one). The sim groups are optional: files without them are still converted. Both stream each file in chunks of `--step_size` events (default 500) and write one parquet row group per chunk to a single output file (`_chunk_to_table` casts every chunk to the first chunk's arrow schema; `--max_events` limits a test run) — loading a whole 10k-event file at once exceeds the 34 GB lxplus memory cgroup. The `-split` variant additionally splits each event into one row per HGCAL endcap and mirrors the negative endcap (`z -> -z`, `eta -> -eta`); it remaps all cross-collection indices (RecHit→LC, LC→Trackster, Trackster→TICLCand, SimTrackster→LC, SimTrackster `seedIndex`→SimCluster when SimCluster-seeded, SimTICLCand→SimTrackster, and both directions of the reco↔sim association links) accordingly via jagged-array prefix-sum bookkeeping; SimTracksters are assigned to an endcap by the seed's `boundaryZ` (falling back to the barycenter), `SimTICLCand` reuses the `ticlSimTrackstersFromCPs` mask, and CaloParticle-seeded `seedIndex` values keep the original (un-split) `CaloPart` index since CaloParticles are not written to parquet, and assumes `CAND_TRACKSTER_COLLECTION = "ticlTrackstersLinks"` is the collection `Candidate2TrackstersIndices` points into — verify this against the production if candidate/trackster indexing looks wrong.

Both scripts have an input-schema layer (`--schema auto|nanoML|ticlnano`, `detect_schema()`, `configure_schema()`, `read_group()`): `ticlnano` is the upstream CMSSW TICL NanoAOD (same table producers as ours, other table names), whose branches are renamed to the nanoML names as they are read, so the parquet columns stay the usual ones. For that schema `configure_schema()` adds the collections that only exist there (`ticlTracksterInterpretations`, `ticlTracksterLinksSuperclusteringDNN` and their `ByHits` associations, the trackster PCA shape, LayerCluster `layer`/`algoID`/`time`, SimTrackster `caloParticleIdx`/`isPU`) and sets `CAND_TRACKSTER_COLLECTION = "ticlTracksterInterpretations"`; for `nanoML` nothing changes (verified: byte-identical output on a no-PU tau file and on a PU nanoML file). The ticlnano files have no RecHit / SimCluster / CaloParticle / MergedSimCluster tables, no Recovery tracksters and no ByLCs associations, so the split code treats `RecHitHGC`, `SimCluster` and every trackster group as optional. `read_group()` reads in tree order on purpose (the struct field order is part of the parquet schema that `parquet_folder_metadata.py` compares across files). Rename examples (`_ticlnano_name()`): `LayerCluster_x` → `HGCalLayerClusters_position_x`, `TICLCand_*` → `TICLCandidates_*`, `Candidate2TrackstersIndices_tracksterIndex` → `TICLCandidatesExtra_tracksterIndex`, `ticlSimTrackstersFromCPs*` → `ticlSimTrackstersfromCPs*`, `SimTICLCand*` → `SimTICLCandidates*`, `CLUE3DHighToSimTSByHits` → `RecoticlTrackstersCLUE3DHigh2SimSCByHits`, `SimTSCPToLinksByHits` → `SimCP2ticlTracksterLinksByHits`; whatever the file lacks (`RecHitHGC`, `SimCluster`, the merged SimClusters, `ticlTrackstersRecovery`, the `ByLCs` tables, the LayerCluster sim matches, `seedIndex`/`seedProductId`/`boundaryTime`) is simply missing from the parquet. In the split, `caloParticleIdx` (row of the SimTrackster's `ticlSimTrackstersFromCPs` entry, one per CaloParticle) is remapped into the split fromCPs collection and is -1 when upstream has none or when the CaloParticle's entry lies in the other endcap (1 of 970 in the test file: a 10 MeV secondary at +z attributed to the −z pion), `eVector0_z` is mirrored with `z`, and the candidate offsets column is written only when the file has one (neither schema does).

### Key Cross-Cutting Concerns

- **D128 geometry**: All configs use `GeometryExtendedRun4D128` — the current Phase-2 HGCal geometry variant (D110 is the prior geometry, still used on `master`).
- **TICL is always on**: unlike `master`, there is no no-TICL RECO path and no standalone TICL-only nano config; `useTICL` in `RECO.py` is a no-op kept for CLI compatibility.
- **`canDeleteEarly`**: Applied via `customiseEarlyDelete(process)` in `nanoML_cfg.py` to reduce peak memory.
- **Output content management**: `outputCommands.append/extend` is used deliberately — dropping a product here will break downstream steps that read it.
- **Pileup discrimination**: signal vs. pileup for SimClusters/MergedSimClusters is derived downstream (in `root_to_parquet*.py`) from `bunchCrossing`/`eventId`, not stored as a flag in the nano tree itself.
- **Pileup chain status**: validated end to end on lxplus (2026-10-25/26). With a realistic 1000-event minbias library and 30 PU, one tau event is GSD 26 MB, RECO 31 MB, nanoML ≈ 3.8 MB (vs 1.2 / 1.5 / 0.15 without PU) and holds ≈ 3500 SimClusters of which ≈ 3400 are pileup; a 10-event library makes the same files 2–3× smaller because the repeated minbias events compress, so never size a campaign from a tiny-library test. `TruthGraphAccumulator` warns in GSD about missing tracker/ME0 sub-event hit collections, which do not exist in the Phase-2 geometry — harmless. Resources and rates: see "Pileup campaign" in the playbook.

## Batch Submission

Scripts live under [`condor/`](condor/). Each replica is an independent `[MINBIAS →] GSD → RECO → nanoML` chain with seed `seed_base + replica_index` (`seed_base = round(energy*1000)` for a fixed `--energy`, `particle*100000 + round(minE*1000)` for a flat `--minE/--maxE` range); when pileup is requested, a prerequisite `MINBIAS` stage runs per replica with seed `signal_seed + 500000`.

### HTCondor + DAGMan (CERN lxplus and LPC)
`submit_dag.sh` detects the site from the hostname (`--site` overrides), runs preflight checks (credentials, output dirs, a 1 MB write probe of `--outdir` and, on CERN EOS, free quota vs. the estimated size of the kept outputs), builds the DAG plus its submit file via `build_dag.sh` (every node runs `run_stage.sh` with `key=value` arguments) and submits it. Required: `--nreplicas`, `--nevents`, and either `--energy` or `--minE`+`--maxE`. Other flags: `--particle`/`--partname`, `--fine-calo`, `--pileup`/`--nminbias`, `--outdir`, `--workdir` (DAG/logs location, AFS not EOS), `--cmssw-base`, `--mode chain|stages`, `--keep`, `--canary N` (a gating N-event chain job all replicas depend on), `--flavour`, `--ncpus`/`--memory`/`--disk`, `--nthreads`, `--seed-base`, `--retry`, `--schedd`, `--rebuild-tarball`, `--dry-run`, `--no-write-test`. Must be run from a shell where `cmsenv` has NOT been sourced (it leaks `PYTHONHOME`/`PYTHONPATH`/`LD_LIBRARY_PATH`, which breaks `condor_submit_dag`).
- **CERN**: CMSSW area used in place from AFS (`--cmssw-base` required when the repo is not inside `$CMSSW_BASE/src`), kerberos forwarded by HTCondor (if `condor_submit` fails with a "credmon did not process credentials" timeout, the CERN credential monitor is stuck: try a fresh `kinit`, otherwise retry later — `submit_dag.sh` only warns about non-renewable tickets), outputs via `xrdcp` to `/eos/...`. Default `--mode chain`: one job per replica runs all stages in its sandbox and stages out only `--keep` (default `nano`) — GSD/RECO intermediates are ~25× the nanoML size.
- **LPC**: CMSSW tarball shipped with each job, VOMS proxy required, outputs to `root://cmseos.fnal.gov/${outdir}/`. Default `--mode stages`: one job per stage and replica, all outputs kept.
Resource footprint (single tau 20–200 GeV, no PU, 1 thread, lxplus): GSD ≈ 4.2 s/event, 4.4 GB peak RSS (5.2 GB with 2 threads) → `--ncpus 2` by default at CERN (3 GB soft limit per core); RECO ≈ 0.45 s/event, 3.3 GB; nanoML ≈ 0.3 s/event, 2.4 GB; sizes ≈ 1.2 / 1.5 / 0.15 MB per event for GSD / RECO / nanoML. CERN batch nodes are ~3× slower than lxplus: a 500-event tau chain job takes ~2h10 (GSD ≈ 13 s/event), a 250-event photon job 40 min at 2 GeV to 2h35 at 300 GeV (GSD time grows with shower energy). See the playbook below.
File names: `${outdir}/{MINBIAS,GSD,RECO,nanoML}/${partname}_{stdCalo,fineCalo}[_PU<N>]_E<min>[to<max>]_rep${N}_<STEP>.root` (the naming rule is duplicated in `build_dag.sh` and `run_stage.sh` — keep them in sync).

### Parquet conversion DAG (CERN)
`submit_parquet.sh` / `run_parquet.sh` convert an existing nanoML campaign to per-file endcap-split parquet on
remote XRootD storage (see "Step 5" above): no CMSSW involved, the jobs use the LCG_109 view from cvmfs
(`--lcg` to change), `+JobFlavour longlunch`, `request_cpus 2`, no explicit memory (`--memory` to set it).

### Falcon — SLURM job array
`submit_pipeline.slurm` runs all 3 stages sequentially inside one job per array task (`sbatch --array=0-N`), since falcon has no DAGMan. Same `--energy`/`--nevents`/`--particle`/`--partname`/`--outdir` flags. **Does not currently support `--pileup`** — PU on falcon needs the same MINBIAS + PU-aware GSD wiring as the condor version; use LPC for pileup campaigns for now.

## Dataset production playbook (October 2026 campaigns)

Two datasets were produced with this branch on CERN HTCondor from lxplus and converted to endcap-split parquet on the CMU T3. This section records exactly what was run, what the files contain and what went wrong along the way, so that a new campaign can be launched without rediscovering it. The generic script options are described in "Batch Submission" above.

### What exists

| Dataset | Events | Gun | Jobs / seeds | nanoML on EOS | Parquet on CMU T3 |
|---|---|---|---|---|---|
| `taus_noFineCalo_with_tracksters_50k` | 50 000 (100 × 500), τ⁻ (pdgId 15) | flat 20–200 GeV, `useFineCalo=0`, no PU | DAG 12877765: 100 chain jobs, seeds 1520000–1520099, plus a 5-event canary (seed 1520100) | `/eos/user/m/mmarcheg/ml4reco/data/taus_noFineCalo_with_tracksters_50k/nanoML/taus_stdCalo_E20to200_rep<0-99>_nanoML.root`, 100 × ~75 MB = 7.4 GB | `root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets/taus_noFineCalo_with_tracksters_50k/`: 100 × `taus_stdCalo_E20to200_rep<N>_split.parquet` + `_metadata`/`_common_metadata`, 9.8 GB |
| `photons_noFineCalo_with_tracksters_<E>GeV_1k`, E ∈ {2, 3, 5, 10, 25, 50, 75, 100, 200, 300} | 1 000 per energy (4 × 250), γ (pdgId 22) | fixed energy E, `useFineCalo=0`, no PU | one DAG per energy, 4 chain jobs each, seeds E·1000 + {0,1,2,3} (2000…, 300000…) | `/eos/user/m/mmarcheg/ml4reco/data/photons_noFineCalo_with_tracksters_<E>GeV_1k/nanoML/photons_stdCalo_E<E>_rep<0-3>_nanoML.root`, 120 MB (2 GeV) → 212 MB (300 GeV) per energy | `.../training_datasets/photons_noFineCalo_with_tracksters_<E>GeV_1k/photons_stdCalo_E<E>_split.parquet` (one merged file per energy) + `_metadata` |

Every file carries the full nanoML content (~600 branches): HGCAL RecHits, LayerClusters, SimClusters / MergedSimClusters / MergedCaloTruthMergedSimClusters, CaloParticles, the four TICL trackster iterations (`ticlTrackstersCLUE3DHigh`, `ticlTracksterLinks`, `ticlTrackstersRecovery`, `ticlTracksterLinksSuperclusteringDNN`), `TICLCand` + `Candidate2Tracksters`, the SimTracksters (`ticlSimTracksters`, `ticlSimTrackstersFromCPs`), `SimTICLCand` + `SimCandidate2Tracksters`, and all reco↔sim association tables (`CLUE3DHighToSimTSByHits`, `SimTSToLinksByLCs`, …). Only nanoML was kept: the GSD and RECO intermediates were deleted in the job sandbox (for the tau set they would have been ~60 GB + ~75 GB). Every file is reproducible from its seed (same gun, fragments and release).

### What one event contains

`GSD_GUN.py` uses pepr's `edm::FlatEtaRangeGunProducer` (`IOMC/ParticleGuns` in the pepr CMSSW area). Per event it shoots `nParticles` particles into the +z endcap **and** `nParticles` into −z (η sign flipped), each with independent flat η ∈ [1.5, 3.0], φ ∈ [−π, π] and energy flat in E (not pT) between `minE` and `maxE`, from the origin (0, 0, 0) with no vertex smearing by the gun. With the default `nParticles=1` an event therefore holds **two showers, one per endcap**: "1k photons" is 1k events = 2k photon showers, and the `-split` parquet writes one row per endcap. The particle is the τ⁻ itself for the tau set (its decay is simulated by Geant4/Pythia inside the detector simulation), a single photon for the photon sets. The `energy=` option of `GSD_GUN.py` is unused; `condor/run_stage.sh` passes `minE`/`maxE` explicitly (`--energy E` in `submit_dag.sh` means `minE=maxE=E`).

### Exact commands

All from a shell where `cmsenv` has NOT been sourced, with a valid kerberos ticket (production) or VOMS proxy (parquet to the T3). The CMSSW release area is `/afs/cern.ch/work/m/mmarcheg/private/ml4reco/phase2/CMSSW_20_1_0_pre4` (pepr overrides of `HLTrigger/NGTScouting`, `SimCalorimetry/HGCalAssociatorProducers`, `DPGAnalysis`, `IOMC`, `reco_prodtools` with the D128 skeletons); it is used in place from AFS by the jobs, so it must stay built and must not be modified while a campaign runs.

```shell
cd production_tests/condor

# tau training set: 100 chain jobs x 500 events; the 5-event canary runs first and
# the replicas only start when it succeeded. Submitted 2026-10-09, done in ~3h45.
bash submit_dag.sh --particle 15 --partname taus --minE 20 --maxE 200 \
    --nreplicas 100 --nevents 500 --canary 5 --ncpus 2 --nthreads 1 --flavour workday \
    --cmssw-base /afs/cern.ch/work/m/mmarcheg/private/ml4reco/phase2/CMSSW_20_1_0_pre4 \
    --outdir /eos/user/m/mmarcheg/ml4reco/data/taus_noFineCalo_with_tracksters_50k \
    --workdir /afs/cern.ch/user/m/mmarcheg/condor/taus_noFineCalo_with_tracksters_50k

# photon evaluation sets: one DAG per energy, 4 x 250 events, no canary
# (the driver /afs/cern.ch/user/m/mmarcheg/condor/photons_noFineCalo_with_tracksters_1k/submit_all.sh
#  runs exactly this loop; "bash submit_all.sh 25" redoes a single energy). Done in ~2h40.
for E in 2 3 5 10 25 50 75 100 200 300; do
    bash submit_dag.sh --particle 22 --partname photons --energy $E \
        --nreplicas 4 --nevents 250 --ncpus 2 --nthreads 1 --flavour workday \
        --cmssw-base /afs/cern.ch/work/m/mmarcheg/private/ml4reco/phase2/CMSSW_20_1_0_pre4 \
        --outdir /eos/user/m/mmarcheg/ml4reco/data/photons_noFineCalo_with_tracksters_${E}GeV_1k \
        --workdir /afs/cern.ch/user/m/mmarcheg/condor/photons_noFineCalo_with_tracksters_1k/E${E}GeV
done

# parquet, streamed to the CMU T3 (needs voms-proxy-init -voms cms). Tau set: one job
# and one *_split.parquet per nanoML file (101 nodes incl. the metadata node, ~2 min per file);
# photon sets: --merge, one job per energy folder producing a single file. Done 2026-10-12.
bash submit_parquet.sh --indir /eos/user/m/mmarcheg/ml4reco/data/taus_noFineCalo_with_tracksters_50k/nanoML \
    --outdir root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets/taus_noFineCalo_with_tracksters_50k \
    --workdir /afs/cern.ch/user/m/mmarcheg/condor/taus_noFineCalo_with_tracksters_50k/parquet
bash submit_parquet.sh --merge /eos/user/m/mmarcheg/ml4reco/data/photons_noFineCalo_with_tracksters_*GeV_1k/nanoML \
    --outdir root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets \
    --workdir /afs/cern.ch/user/m/mmarcheg/condor/photons_noFineCalo_with_tracksters_1k/parquet
```

Monitoring and recovery, from the campaign's `--workdir`:

```shell
condor_q -dag $USER                                   # DAGMan job + node jobs
tail -f pipeline_<sample>.dag.dagman.out              # node submissions / completions
ls <outdir>/nanoML | wc -l                            # files landed on EOS
grep -hE 'completed successfully|failed with' pipeline_*.dagman.out | sort | uniq -c
condor_submit_dag -f pipeline_<sample>.dag            # after failures: rescue DAG, reruns only missing nodes
```
Per-job stdout/stderr (`logs/<node>.out/.err`) is only written back when the job ends; a running job shows nothing. Each log ends with the `cmsRun` step timestamps and the sandbox size.

### Runtime, memory and sizes measured on the batch nodes (2 cores, 1 thread)

- Tau 500-event chain job: GSD ≈ 1h50 (13 s/event, 3× slower than lxplus), RECO ≈ 15 min, nanoML ≈ 7 min, ≈ 2h10 in total; 100 jobs ran concurrently, campaign wall time ≈ 3h45.
- Photon 250-event job: 42 min at 2 GeV (GSD 33 min) to 2h35 at 300 GeV (GSD 2h20); RECO + nanoML ≈ 10 min. GSD time grows with shower energy, so size jobs for the highest energy.
- Peak RSS: GSD 4.4 GB (1 thread) / 5.2 GB (2 threads), RECO 3.3 GB, nanoML 2.4 GB. CERN slots give 3 GB per core as a soft limit, hence `--ncpus 2` (6 GB); `--nthreads 1` keeps the headroom.
- Output: nanoML ≈ 0.15 MB/event for taus (75 MB per 500 events, 7.4 GB for 50k), 0.12–0.21 MB/event for photons from 2 to 300 GeV; endcap-split parquet ≈ 190 KB/event (9.8 GB for the tau set, 313 columns). Sandbox usage per job stays below 2 GB (GSD + RECO of 500 events) and is cleaned at the end.
- `+JobFlavour = "workday"` (8 h) is comfortable for 500 taus or 250 photons; do not go beyond ~1500 tau events or ~700 300-GeV photons per job without `tomorrow`.

### Pileup campaign: taus 10k at 30 PU (prepared 2026-10-26, NOT yet submitted)

An interactive `scripts/generate_taus_noFineCalo_30PU.sh <eos dir> 10000 --nminbias 1000` run died in GSD after 1974 events with
`FileWriteError: syswrite() failed with system error 'File too large' (error code 27)`: that is how the EOS FUSE mount reports
the exhausted home quota (100 %, "exceeded"), and with 30 PU the GSD file alone would have been 250 GB. Pileup intermediates can
only live in a condor sandbox, so the campaign is the condor chain mode with `--keep nano`. Everything is prepared in
`/afs/cern.ch/user/m/mmarcheg/condor/taus_noFineCalo_with_tracksters_30PU_10k/` (`submit.sh` runs the exact command below, the
DAG/JDL are built): it was not submitted because `/eos/user/m/mmarcheg` has no free quota, the account is not in `cms-eos-hgcal`
(`eos root://eoscms.cern.ch member cms-eos-hgcal` → false) and no VOMS proxy exists for the CMU T3 (`build_dag.sh` would also
need to ship a proxy at CERN, as `submit_parquet.sh` does). The user's 1000-event minbias library from the interactive run is at
`/eos/user/m/mmarcheg/ml4reco/data/taus_noFineCalo_with_tracksters_30PU_10k/taus_stdCalo_PU30_MINBIAS.root` (1.8 GB).

```shell
# needs ~40 GB free under --outdir (nanoML ≈ 3.8 MB/event at 30 PU); OUTDIR=<other area> bash submit.sh to redirect
bash submit_dag.sh --particle 15 --partname taus --minE 20 --maxE 200 --pileup 30 --nminbias 100 \
    --nreplicas 100 --nevents 100 --canary 5 --ncpus 3 --nthreads 1 --flavour tomorrow --seed-base 1620000 \
    --cmssw-base /afs/cern.ch/work/m/mmarcheg/private/ml4reco/phase2/CMSSW_20_1_0_pre4 \
    --outdir /eos/user/m/mmarcheg/ml4reco/data/taus_noFineCalo_with_tracksters_30PU_10k \
    --workdir /afs/cern.ch/user/m/mmarcheg/condor/taus_noFineCalo_with_tracksters_30PU_10k
```

Why these numbers (measured on lxplus with 1 thread and the 1000-event library, 2026-10-26):
- Peak RSS: GSD 6.0 GB, RECO 4.3 GB, nanoML 3.2 GB → `--ncpus 3` (9 GB at 3 GB/core; 2 cores would sit at the limit).
- Rates: minbias 4.5 s/event, GSD 27 s/event at 1 thread (the user's multi-threaded interactive runs did 12.6 s/event), RECO ≈ 6 s/event after a 13 s first event, nanoML ≈ 5 s/event (one run showed a 4-minute first event, not reproduced — lxplus I/O). With the ≈ 3× batch slowdown a 100-event job is ≈ 25 min minbias + 2h15 GSD + 35 min RECO + 15 min nanoML ≈ 3.5 h → `--flavour tomorrow` (workday would probably do, with a 2× margin).
- Sizes per event: minbias 1.9 MB, GSD 26 MB, RECO 31 MB, nanoML ≈ 3.8 MB (10k events ≈ 40 GB of nanoML, endcap-split parquet probably ≈ 50 GB). Sandbox peak ≈ 6 GB per 100-event job (default `request_disk` 20 GB). `submit_dag.sh` scales its quota estimate from these PU 30 numbers.
- `--nminbias 100` = one 100-event library per job (seed + 500000), i.e. 10k distinct minbias events over the dataset — more variety than the single 1000-event library of the interactive run, for 25 min of minbias generation per job. Each signal event draws ≈ 16 bunch crossings × 30 events from the library, so minbias events are reused within a job whatever the size.
- `--seed-base 1620000`: the default (particle·100000 + minE·1000 = 1520000) is the no-PU tau campaign's, which would reproduce exactly the same 10k signal taus (the gun and Geant4 seeds precede the mixing); 1620000–1620100 (minbias 2120000–2120100) are disjoint from everything produced so far. If a paired with/without-PU sample is ever wanted, drop the option.
- nanoML runs with `runMergedSimClusters=0` (set by `run_stage.sh` from `pileup=30`): no `MergedSimCluster` tables, see "nanoML_cfg.py" above.

### Lessons learned

1. **Storage before submission.** The CERN EOS home (1 TB) was at 99.9 % and the AFS work area (100 GB) at 100 % when the tau campaign was prepared; `/eos/cms/store/user/<user>` did not exist and `/eos/cms/store/group/hgcal/ML4Reco` (76 TB free, used by colleagues) is only writable for members of the `cms-eos-hgcal` e-group. `submit_dag.sh` now probes a 1 MB write and compares the free `eos quota` with the estimated output; it refuses to submit when the kept outputs do not fit. Keep only nanoML on EOS and put parquet on the CMU T3. Put `--workdir` on the AFS home (DAG files and logs cannot live on EOS).
2. **"The credmon did not process credentials within the timeout period".** Every `condor_submit` on lxplus failed with this for ~45 minutes on 2026-10-09, on two schedds; `condor_store_cred query-krb` showed the credential "waiting for processing". It is a CERN-side credential-monitor problem: retry (a 5-minute retry loop around `condor_submit_dag` worked), do a fresh `kinit`, and if it lasts open a ticket with the batch service. `myschedd bump` does not help. A forwarded, non-renewable ticket (no `R` in `klist -f`) submitted fine later the same day, so the ticket flags are not a requirement.
3. **Use the canary for a new configuration** (new particle, energy range, fragment or release): a broken setup then fails once instead of 100 times. The photon DAGs skipped it because they reused the tau configuration unchanged.
4. **Seeds must stay disjoint across campaigns**: fixed energy → E·1000 + replica, flat range → particle·100000 + minE·1000 + replica, minbias → signal seed + 500000, canary → seed base + nreplicas. The one-shot scripts in `scripts/` default to `--seed 1`; do not mix their outputs with batch outputs without changing it. `seed` also sets the first luminosity block, so (run 1, lumi seed+1, event n) is unique per job.
5. **Intermediates are gone by design** (`--keep nano`). Pass `--keep nano,reco` or `--keep nano,reco,gsd` to a *new* submission if they are needed; a running campaign cannot be changed. Account for ≈ 1.2 (GSD) + 1.5 (RECO) MB per event.
6. **No FineCalo in these sets**: `useFineCalo=0` means the standard CaloTruth (`mix:MergedCaloTruth` SimClusters/CaloParticles and the `MergedCaloTruthMergedSimCluster` collection); SimTracksters are seeded from those. FineCalo variants (`--fine-calo`) are a separate configuration and do not support pileup.
7. **Chain mode vs stages mode**: at CERN one job runs GSD → RECO → nanoML (default `--mode chain`); the per-stage layout (`--mode stages`, LPC default) stages every intermediate out and back and is only useful where storage is plentiful.
8. **"File too large" on EOS means quota, and pileup means chain mode.** cmsRun writing to `/eos/user/...` through FUSE fails with `FileWriteError ... 'File too large' (error code 27)` when the quota node is exhausted; `eos root://eosuser.cern.ch quota /eos/user/<u>/<user>` shows it. With pileup the GSD/RECO files are ≈ 20× the no-PU ones (26 / 31 MB per event at PU 30), so never run a pileup production interactively onto EOS: use the condor chain with `--keep nano` and budget ≈ 4 MB per event of nanoML.

## Hackathon TICL v6 NanoAOD (October 2026)

The hackathon organizer's samples live under `/eos/cms/store/group/dpg_hgcal/comm_hgcal/wredjeb/CMSSW_20_1_0_pre4_ticlv6/`:
`SinglePiPt25Eta1p7_2p7/SinglePiPt25Eta1p7_2p7_D121/step3_inNANOAODSIM/` (40 files × 100 events, 290 MB each) and
`TenTau_E15to500_Eta3p1/TenTau_E15to500_Eta3p1_D121/step3_inNANOAODSIM/` (38 files), plus `_full` variants with 10 files each
that also keep `step2` (DIGI-RAW, 4.5 GB per 100 events) and `step3` (RECO with RecHits, `mix:MergedCaloTruth` SimClusters/CaloParticles, all
TICL collections and associations, 5.5 GB per 100 events). They are **200 pileup** samples in the D121 geometry
(≈ 124k LayerClusters, 3150 CLUE3D tracksters, 1770 `ticlTracksterInterpretations`, 1560 TICLCandidates and 7700 tracks
per event; 2 gun particles per event, one per endcap; ≈ 10 signal SimTracksters, `isPU` is 0 everywhere because the PU
SimTracksters are not dumped). The step3_inNANOAODSIM files are genuine NanoAOD (Events/LuminosityBlocks/Runs/MetaData/
ParameterSets, 459 flat branches, `n<table>` counters) made of the upstream TICL tables: `HGCalLayerClusters`,
`ticlTrackstersCLUE3DHigh`/`ticlTracksterLinks`/`ticlTracksterLinksSuperclusteringDNN`/`ticlTracksterInterpretations`
(+ `<name>vertices` tables), `TICLCandidates` (+ `Extra`, pointing into `ticlTracksterInterpretations`, counts only, no
offsets), `SimTICLCandidates`, `ticlSimTracksters`/`ticlSimTrackstersfromCPs` (lower-case f; `caloParticleIdx`, `isPU`,
no seedIndex; `boundaryZ` is 0 for 16 % of the fromCPs entries, for which the split falls back to the barycenter), `Reco<iteration>2Sim{SC,CP}ByHits` / `Sim{SC,CP}2<iteration>ByHits` associations (no ByLCs),
`HGCalGenPart`, `SimCl2CPWithFraction`, `GeneralTrack`, `GSFTrack`, `TrackMuonInfo`. There are **no RecHit, SimCluster,
CaloParticle or MergedSimCluster tables**, so only the trackster-level training (`hgcal-offline-reco`
`configs/base_trackster_simtrackster.yaml`: CLUE3D tracksters → SimTracksters via `CLUE3DHighToSimTSByHits`) can use them,
and even that config reads the SimTrackster pdgId off the `SimCluster` table through `seedIndex`
(`PARTICLE_SEED_LOOKUP` in `src/dataset/collections.py`), which these files cannot provide: the classification target
needs another source (e.g. `HGCalGenPart` for the two gun particles) or a config without it, and `max_nodes` must grow
from 64 to the PU 200 multiplicity (≈ 2100 CLUE3D tracksters per endcap). `root_to_parquet*.py` convert them with
`--schema ticlnano` (auto-detected); checked 2026-10-26 on `step3_inNANOAODSIM_1440986_0.root`: 100 events → 200 endcap rows,
25 groups, every per-event count conserved and every remapped index in range, ≈ 1 min and 2.6 GB RSS per 100-event chunk,
347 MB of parquet (≈ 3.5 MB per input event: the 124k LayerClusters dominate); 12 of 970 SimTracksters end with
`caloParticleIdx = -1` (11 upstream, 1 whose CaloParticle entered the other endcap). The nanoML output is byte-identical to before. The RecHit-level formats could only be produced by
running `nanoML_cfg.py` on the `_full/step3` RECO files, which needs the D121 geometry (`pre1_D121` branch configs) and a
check that the pepr-patched SimCluster class can read the vanilla files.

## MC truth
We are using this repo to produce the samples for our MaskFormer training. Different possibilities for the MC truth are possible:
- SimCluster
- MergedSimCluster
- MergedCaloTruthMergedSimCluster

Recently, new collections have been implemented in CMSSW for the MC truth. Read the email in the file FineCalo_JanKieseler.eml to understand the context of these latest developments.
