# production_tests — CMSSW_20_0_0_pre1 branch

Simple production scripts for HGCAL Phase 2 studies on the pepr fork of
CMSSW_20_0_0_pre1 with the `Run4D121` geometry. Fork of
[kdlong/production_tests](https://github.com/kdlong/production_tests),
adapted for pre1 and the new default TICL reconstruction.

For the `CMSSW_15_1_0` / `Run4D110` version, see the `master` branch.

## Setup

```shell
export SCRAM_ARCH=el9_amd64_gcc13
version=CMSSW_20_0_0_pre1
cmsrel $version
cd $version/src
cmsenv
git cms-init -y
git cms-merge-topic dgaytanv:pepr_${version}
scram b -j 4      # -j 12 can OOM on cmslpc

# Reco fragments
git clone git@github.com:dgaytanv/reco-prodtools.git reco_prodtools
cd reco_prodtools/templates/python
./produceSkeletons_D121.sh
cd ../../..

# This repo (pre1_D121 branch)
git clone -b pre1_D121 git@github.com:dgaytanv/production_tests.git
cd production_tests
```

## Workflow

Three sequential `cmsRun` stages produce a nanoAOD flat ntuple with the
full HGCAL truth chain, TICL reconstruction, and their associations.

### 1. GSD (Generate → Sim → Digi)

`GSD_GUN.py` fires a configurable particle gun into HGCAL and runs
GEN + SIM + DIGI. Before running, edit the file to set particle IDs,
energy range, and eta range. `useFineCalo=1` (default is 0) enables the
fineCalo boundary-crossing SimCluster/CaloParticle producer.

```shell
cmsRun GSD_GUN.py seed=1 outputFile=testGSD.root useFineCalo=1
```

Options:
- `seed=N` — random seed and first lumiBlock (required for unique output)
- `nParticles=N` — particles per event
- `useFineCalo={0,1}` — fineCalo on/off
- `pileup=N` — average PU (0 = no PU). Requires `pu=<file>` when > 0
  (see "MINBIAS" section below).
- `pu=<file>` — minbias GEN-SIM file to mix as pileup. Path can be
  plain (`smoke.root`), `file:` URL, or `root://` URL.
- `maxEvents=N`, `nThreads=N`

### 1a. Optional — MINBIAS (only when running with pileup)

For pileup mixing, generate a minbias GEN-SIM library first, then feed
it into `GSD_GUN.py` via the `pu=` option:

```shell
# Step 1: minbias library. nEvents should be >= average PU for good
# statistics. Use a distinct seed from your signal run.
cmsRun MINBIAS_GENSIM.py seed=42 maxEvents=200 outputFile=pileup_gensim.root

# Step 2: GSD with PU mixing. pileup=N sets the average, pu=<file>
# points to the minbias library from step 1.
cmsRun GSD_GUN.py seed=1 particle=22 energy=100 pileup=30 useFineCalo=1 \
    pu=pileup_gensim.root outputFile=testGSD.root
```

`MINBIAS_GENSIM.py` runs Pythia8 SoftQCD (non-diffractive + single +
double diffractive) at 14 TeV with the CP5 tune. When `pileup > 0`,
`GSD_GUN.py` routes through `GSD_fragment_PU` (a cmsDriver-generated
fragment with `--pileup AVE_30_BX_25ns` baked in) instead of the
no-PU `GSD_fragment`.

The batch-submission scripts under `condor/` handle this automatically
via the `--pileup` and `--nminbias` flags — see the batch submission
section below.

### 2. RECO (Reconstruction)

`RECO.py` runs standard Phase 2 reconstruction on GSD output. TICL is
the default reco path in pre1, so all trackster collections
(`ticlTrackstersCLUE3DHigh`, `ticlTrackstersRecovery`,
`ticlTracksterLinks`, `ticlTracksterLinksSuperclusteringDNN`) and the
final `ticlCandidate` are always in the output.

```shell
cmsRun RECO.py inputFiles=file:testGSD.root outputFile=testRECO.root
```

Options:
- `useTICL={0,1}` — kept for CLI compatibility with the 15_1_0 branch,
  no-op in pre1. TICL is always on.

The RECO output also runs the pepr `SimClusterMerger` (mergedSimClusters
alongside legacy SimClusters and boundary/CaloParticle SimClusters) and
the standard HGCAL associations (LC↔CP, LC↔SC).

### 3. nanoML (flat ntuples)

`nanoML_cfg.py` reads RECO output and produces a nanoAOD flat file with
372 branches covering GEN, sim truth, HGCAL rechits, layer clusters,
tracksters, and TICLCandidates, plus their pairwise index associations.

```shell
cmsRun nanoML_cfg.py inputFiles=file:testRECO.root outputFile=testNanoML.root
```

Options:
- `runPFTruth={0,1}` — PFTruth sequence (currently broken with pileup).

## Nano output — the RecHit → TICLCand chain

The chain from RecHits to TICLCandidates is available via three index
associations in the flat tree, plus per-object kinematic tables.

### Batch submission — condor (LPC) and SLURM (falcon)

For large parallel campaigns (many replicas at a fixed energy) use the
scripts under [`condor/`](condor/). Each replica is an independent chain
`GSD → RECO → nanoML` with a unique seed
(`seed = round(energy * 1000) + replica_index`). When `--pileup > 0` a
prerequisite `MINBIAS` stage is added: each replica independently
generates its own minbias GEN-SIM library (with an offset seed of
`signal_seed + 500000`) that gets mixed into the GSD step.

#### LPC — HTCondor + DAGMan

```shell
# One-time setup (in a shell where cmsenv has NOT been sourced;
# cmsenv leaks PYTHONHOME/PYTHONPATH/LD_LIBRARY_PATH which breaks
# condor_submit_dag):
cd $CMSSW_BASE/src/production_tests/condor
voms-proxy-init -voms cms -valid 192:00

# Smoke test: 1 replica, 2 events at 10 GeV; defaults to
# /store/user/$USER/production_tests/pre1_D121
bash submit_dag.sh --energy 10 --nreplicas 1 --nevents 2

# Full campaign: 10 replicas at 100 GeV, 200 events each
bash submit_dag.sh --energy 100 --nreplicas 10 --nevents 200

# Override output location (EOS path or shared FS)
bash submit_dag.sh --energy 100 --nreplicas 10 --nevents 200 \
    --outdir /store/user/${USER}/mycampaign

# Different particle
bash submit_dag.sh --energy 100 --nreplicas 10 --nevents 200 \
    --particle 11 --partname electron
```

Options:

| Flag                | Default                                                   |
|---------------------|-----------------------------------------------------------|
| `--energy`          | *required* (GeV)                                          |
| `--nreplicas`       | *required*                                                |
| `--nevents`         | *required*, events per job                                |
| `--particle`        | `22` (photon pdgId)                                       |
| `--partname`        | `photon` (used for file naming)                           |
| `--outdir`          | `/store/user/$USER/production_tests/pre1_D121`            |
| `--pileup`          | `0` (no PU). Set > 0 to enable minbias mixing.            |
| `--nminbias`        | equal to `--nevents`. Minbias events per replica.         |
| `--schedd`          | (unset) — pass e.g. `lpcschedd4.fnal.gov` to pin a schedd |
| `--rebuild-tarball` | force rebuild the CMSSW tarball                           |

Outputs land at
`root://cmseos.fnal.gov/${outdir}/{GSD,RECO,nanoML}/${partname}_E${energy}_rep${N}_*.root`
(or under the local path if `--outdir` is a plain filesystem path). When
`--pileup > 0`, an additional `MINBIAS/` subdirectory is populated with
the minbias GEN-SIM libraries used for the mix.

Monitor / retry:

```shell
condor_q -dag $USER
tail -f pipeline_${partname}_E${energy}.dag.dagman.out

# Retry from where it left off (uses rescue file)
condor_submit_dag -f pipeline_${partname}_E${energy}.dag

# Full reset (empty the queue first)
condor_rm $USER
rm -f pipeline_*.dag.* pipeline_*.rescue*
```

#### Falcon — SLURM job array

Falcon has no DAGMan, so the SLURM version runs all 3 stages
sequentially inside one job per array task. Parallelism comes from
`--array=0-N`, one array task per replica.

```shell
cd $CMSSW_BASE/src/production_tests/condor
mkdir -p logs

sbatch --array=0-9 submit_pipeline.slurm \
    --energy 100 --nevents 200 \
    --outdir /home/export/$USER/production_tests/pre1_D121
```

Note: the SLURM version does not currently support `--pileup`. PU
support in SLURM will require the same MINBIAS + PU-aware GSD wiring
as the condor version; not yet implemented. Run with `--pileup > 0`
on LPC only for now.

Same `--particle`/`--partname` flags as the LPC version. If your
CMSSW is not at `$HOME/CMSSW_20_0_0_pre1`, pass
`--cmssw-base /path/to/CMSSW_20_0_0_pre1`.

### root → parquet conversion

[`root_to_parquet.py`](root_to_parquet.py) flattens nanoML ROOT files
into a single parquet file for ML training. Collections are grouped
by object family (`RecHitHGC`, `LayerCluster`, `SimCluster`,
`MergedSimCluster`, `TICLCand`, `Candidate2Tracksters`, and each
trackster iteration).

Two derived boolean fields are added:
`SimCluster_isPileup` and `MergedSimCluster_isPileup`, computed as
`~((bunchCrossing == 0) & (eventId == 0))`.

```shell
python3 root_to_parquet.py \
    --nanoMLfiles /path/to/nanoML1.root /path/to/nanoML2.root \
    --outputDir parquet_out \
    --outputFile mydata.parquet
```
