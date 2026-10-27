# production_tests — CMSSW_20_1_0_pre4 branch

Simple production scripts for HGCAL Phase 2 studies on the pepr fork of
CMSSW_20_1_0_pre4 with the `Run4D128` geometry. Fork of
[kdlong/production_tests](https://github.com/kdlong/production_tests),
adapted for CMSSW 20_1 and the default TICL reconstruction.

For the `CMSSW_20_0_0_pre1` / `Run4D121` version, see the `pre1_D121` branch;
for `CMSSW_15_1_0` / `Run4D110`, see the `master` branch.

## Setup

```shell
export SCRAM_ARCH=el9_amd64_gcc14
version=CMSSW_20_1_0_pre4
cmsrel $version
cd $version/src
cmsenv
git cms-init -y
git cms-merge-topic dgaytanv:pepr_${version}
git cms-checkdeps -a   # pepr changes the SimCluster layout: rebuild every package that uses it
scram b -j 4      # -j 12 can OOM on cmslpc

# Reco fragments
git clone git@github.com:dgaytanv/reco-prodtools.git reco_prodtools
cd reco_prodtools/templates/python
./produceSkeletons_D128.sh
cd ../../..
scram b           # registers reco_prodtools.templates.* as python modules

# This repo (pre4_D128 branch)
git clone -b pre4_D128 git@github.com:dgaytanv/production_tests.git
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
- `gun={flatEta,closeBy}` — `flatEta` (default) shoots from the origin,
  `nParticles` into each endcap, |η| 1.5–3.0. `closeBy` starts `nParticles`
  on the +z HGCAL front face (R 60–120 cm, z 320–321 cm) pointing away from
  the origin, so the particle never crosses the tracker; this matches the
  earlier GNN energy-resolution samples.
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
- `runMergedSimClusters={0,1}` — pepr's `SimClusterMerger` (`hgcSimTruth`) and the
  `MergedSimCluster` tables (default 1). Must be 0 for pileup samples: the merger
  only knows the signal `g4SimHits` SimTracks/SimVertices and throws
  (`vector::_M_range_check`) on the first pileup SimCluster. The pileup-aware
  `MergedCaloTruthMergedSimCluster` tables are not affected. `scripts/chain_common.sh`
  and `condor/run_stage.sh` set it automatically from the pileup value.

## Nano output — the RecHit → TICLCand chain

The chain from RecHits to TICLCandidates is available via three index
associations in the flat tree, plus per-object kinematic tables.

### Batch submission — HTCondor (CERN lxplus / LPC) and SLURM (falcon)

For large parallel campaigns use the scripts under [`condor/`](condor/).
Each replica is an independent chain `[MINBIAS →] GSD → RECO → nanoML`
with a unique seed `seed_base + replica_index`, where `seed_base` is
`round(energy * 1000)` for a fixed `--energy` and
`particle * 100000 + round(minE * 1000)` for a flat `--minE`/`--maxE`
range (override with `--seed-base`). When `--pileup > 0` a prerequisite
`MINBIAS` stage generates one minbias GEN-SIM library per replica (seed
offset `+500000`) that gets mixed into the GSD step.

#### HTCondor + DAGMan (`submit_dag.sh`)

`submit_dag.sh` detects the site from the hostname (`--site cern|lpc`
overrides it), builds the DAG with `build_dag.sh` — which also renders the
submit file `pipeline_<sample>.jdl` — runs a few preflight checks
(credentials, output directories, a 1 MB write probe of `--outdir` and, on
CERN EOS, free quota vs. the estimated size of the kept outputs) and
submits. Every DAG node runs `run_stage.sh` with `key=value` arguments.

- **CERN (lxplus)** — the CMSSW area is used in place from AFS (no
  tarball; pass `--cmssw-base` when this repo is not checked out inside
  `$CMSSW_BASE/src`), HTCondor forwards the kerberos ticket, outputs are
  written to `/eos/...` with `xrdcp`. If `condor_submit` fails with
  "store_cred of Kerberos credential failed - The credmon did not process
  credentials within the timeout period", the CERN credential monitor is
  not processing credentials; a fresh `kinit` (renewable ticket) is the
  first thing to try, otherwise retry later. Default `--mode chain`: one job per
  replica runs all stages inside its sandbox and stages out only the
  products listed in `--keep` (default `nano`, i.e. just the nanoML file;
  the intermediate GSD/RECO files are ~25× larger).
- **LPC (cmslpc)** — a tarball of the CMSSW area is built once and shipped
  with every job, a VOMS proxy is required, outputs go to `/store/user/...`
  on cmseos. Default `--mode stages`: one job per stage and replica, every
  stage output kept and fetched back by the next stage.

```shell
# Always from a shell where cmsenv has NOT been sourced (it leaks
# PYTHONHOME/PYTHONPATH/LD_LIBRARY_PATH, which breaks condor_submit_dag).

# LPC smoke test: 1 replica, 2 events at 10 GeV; defaults to
# /store/user/$USER/production_tests/pre4_D128
cd $CMSSW_BASE/src/production_tests/condor
voms-proxy-init -voms cms -valid 192:00
bash submit_dag.sh --energy 10 --nreplicas 1 --nevents 2

# LPC full campaign: 10 replicas at 100 GeV, 200 events each
bash submit_dag.sh --energy 100 --nreplicas 10 --nevents 200

# CERN: 50k taus flat in 20-200 GeV as 100 jobs x 500 events, nanoML only,
# gated by a 5-event canary job that must succeed before the replicas start
bash submit_dag.sh --particle 15 --partname taus --minE 20 --maxE 200 \
    --nreplicas 100 --nevents 500 --canary 5 \
    --cmssw-base /afs/cern.ch/work/u/user/CMSSW_20_1_0_pre4 \
    --outdir /eos/user/u/user/taus_50k \
    --workdir /afs/cern.ch/user/u/user/condor/taus_50k

# Build the DAG and run the preflight checks without submitting
bash submit_dag.sh ... --dry-run
```

Options (`submit_dag.sh` accepts every `build_dag.sh` option):

| Flag                | Default                                                                 |
|---------------------|-------------------------------------------------------------------------|
| `--nreplicas`       | *required*                                                              |
| `--nevents`         | *required*, signal events per replica                                   |
| `--energy`          | fixed gun energy (GeV); or `--minE`/`--maxE` for a flat range (one of the two forms is required) |
| `--particle`        | `22` (photon pdgId)                                                     |
| `--partname`        | `photon` (used for file naming)                                         |
| `--fine-calo`       | off (`useFineCalo=0`); incompatible with `--pileup`                     |
| `--gun`             | `flatEta`; `closeBy` starts the particle on the HGCAL front face (see GSD options) |
| `--pileup`          | `0` (no PU). Set > 0 to enable minbias mixing. With pileup use `--ncpus 3` (GSD peaks at 6 GB RSS at PU 30) and budget ≈ 4 MB/event of nanoML; GSD/RECO are 26/31 MB per event, which is why only the chain mode is viable (see the pileup campaign in CLAUDE.md). |
| `--nminbias`        | equal to `--nevents`. Minbias events per replica.                       |
| `--outdir`          | `/store/user/$USER/production_tests/pre4_D128` (LPC), `/eos/user/<u>/$USER/production_tests/pre4_D128` (CERN) |
| `--workdir`         | `condor/` — where the DAG, submit file and `logs/` are written; must not be on EOS |
| `--cmssw-base`      | `../../..` relative to this repo (= `$CMSSW_BASE` when it sits in `src/`) |
| `--mode`            | `chain` (CERN) / `stages` (LPC)                                         |
| `--keep`            | `nano` — chain mode: subset of `minbias,gsd,reco,nano` to stage out     |
| `--canary`          | `0` (off) — events for a gating chain job all replicas depend on        |
| `--flavour`         | `workday` (CERN `+JobFlavour`; LPC uses `+MaxRuntime = 86400`)          |
| `--ncpus`           | `2` at CERN (GSD peaks at ~4.4 GB RSS, above the 3 GB-per-core slot default), `1` at LPC |
| `--memory`          | CERN: unset (3 GB per requested core); LPC: `5000` MB                   |
| `--disk`            | `20000000` KB                                                           |
| `--nthreads`        | `1` thread per cmsRun step                                              |
| `--seed-base`       | see above                                                               |
| `--retry`           | `2` DAGMan retries per node                                             |
| `--schedd`          | (unset) — pass e.g. `lpcschedd4.fnal.gov` to pin a schedd               |
| `--rebuild-tarball` | force rebuild of the CMSSW tarball (LPC)                                |
| `--dry-run`         | build the DAG and run the preflight checks, do not submit               |
| `--no-write-test`   | skip the 1 MB write probe and the EOS quota check of `--outdir`         |

Outputs land at
`${outdir}/{MINBIAS,GSD,RECO,nanoML}/<partname>_<stdCalo|fineCalo>[_PU<N>]_E<min>[to<max>]_rep<N>_<STEP>.root`
(chain mode writes only the kept products; the canary writes under
`${outdir}/canary/`). `/store/...` paths go through `cmseos.fnal.gov`,
`/eos/user` and `/eos/cms` paths through `eosuser.cern.ch` /
`eoscms.cern.ch`; anything else is treated as a shared filesystem.

Resource footprint per job (single tau, flat 20–200 GeV, no PU, 1 thread,
measured Sep/Oct 2026 on lxplus): GSD ≈ 4.2 s/event and 4.4 GB peak RSS
(5.2 GB with 2 threads), RECO ≈ 0.45 s/event and 3.3 GB, nanoML ≈ 0.3 s/event
and 2.4 GB, plus a few minutes of initialisation per step. Output sizes ≈
1.2 MB/event (GSD), 1.5 MB/event (RECO), 0.15 MB/event (nanoML), i.e. a
500-event chain job takes ~45 min and leaves a 75 MB nanoML file.

Monitor / retry:

```shell
condor_q -dag $USER
tail -f <workdir>/pipeline_<sample>.dag.dagman.out

# Retry from where it left off (uses rescue file)
cd <workdir> && condor_submit_dag -f pipeline_<sample>.dag

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
    --outdir /home/export/$USER/production_tests/pre4_D128
```

Note: the SLURM version does not currently support `--pileup`. PU
support in SLURM will require the same MINBIAS + PU-aware GSD wiring
as the condor version; not yet implemented. Run with `--pileup > 0`
on LPC only for now.

Same `--particle`/`--partname` flags as the LPC version. If your
CMSSW is not at `$HOME/CMSSW_20_1_0_pre4`, pass
`--cmssw-base /path/to/CMSSW_20_1_0_pre4`.

### root → parquet conversion

[`root_to_parquet.py`](root_to_parquet.py) flattens nanoML ROOT files
into a single parquet file for ML training. Collections are grouped
by object family (`RecHitHGC`, `LayerCluster`, `SimCluster`,
`MergedSimCluster`, `MergedCaloTruthMergedSimCluster`, `TICLCand`,
`Candidate2Tracksters`, each trackster iteration, the MC-truth
tracksters `ticlSimTracksters` / `ticlSimTrackstersFromCPs`, the sim
candidates `SimTICLCand` / `SimCandidate2Tracksters`, and the reco↔sim
trackster association tables such as `CLUE3DHighToSimTSByHits`).

Derived boolean fields are added: `SimCluster_isPileup`,
`MergedSimCluster_isPileup` and
`MergedCaloTruthMergedSimCluster_isPileup`, computed as
`~((bunchCrossing == 0) & (eventId == 0))`, plus
`ticlSimTracksters_seedIsCaloParticle` /
`ticlSimTrackstersFromCPs_seedIsCaloParticle`, telling whether a
SimTrackster's `seedIndex` points into the `CaloPart` table (true) or
into the `SimCluster` table (false).

```shell
python3 root_to_parquet.py \
    --nanoMLfiles /path/to/nanoML1.root /path/to/nanoML2.root \
    --outputDir parquet_out \
    --outputFile mydata.parquet
```

Both scripts stream the input in chunks of `--step_size` events (default
500) and append each chunk to the single output file as one parquet row
group, so peak memory is set by the chunk size rather than by the file
size (a 10k-event tau file needs > 34 GB if loaded at once). Readers can
therefore iterate the file row group by row group
(`ak.from_parquet(file, row_groups=[...])`). `--max_events N` converts
only the first N events of each file, for quick tests.

### Converting the upstream TICL NanoAOD (`--schema ticlnano`)

Both converters also read the upstream CMSSW TICL NanoAOD, e.g. the TICL v6
hackathon files (CMSSW_20_1_0_pre4, D121, 200 pileup) under
`/eos/cms/store/group/dpg_hgcal/comm_hgcal/wredjeb/CMSSW_20_1_0_pre4_ticlv6/<sample>/<sample>_D121/step3_inNANOAODSIM/`
(100 events per file). The flavour is detected from the first file (`--schema
auto`, or force `nanoML` / `ticlnano`); the branches are renamed to the nanoML
names as they are read, so the parquet columns are the usual ones
(`LayerCluster_*`, `TICLCand_*`, `ticlTrackstersCLUE3DHigh_*`,
`ticlSimTrackstersFromCPs_*`, `CLUE3DHighToSimTSByHits`, ...). Differences
with respect to a nanoML conversion:

- no `RecHitHGC`, `SimCluster`, `MergedSimCluster`,
  `MergedCaloTruthMergedSimCluster` groups and no LayerCluster sim matches:
  those tables do not exist upstream, so the RecHit-level trainings cannot use
  these files (the `_full` sibling folders hold the RECO with RecHits and
  SimClusters, from which `nanoML_cfg.py` could be run instead);
- no `ticlTrackstersRecovery` and no `ByLCs` association tables; instead the
  TICL v6 `ticlTracksterInterpretations` and
  `ticlTracksterLinksSuperclusteringDNN` tracksters with their `ByHits`
  associations (`InterpretationsToSimTSByHits`, `SuperclusteringDNNToSimTSCPByHits`, ...);
  `Candidate2Tracksters` points into `ticlTracksterInterpretations`;
- SimTracksters carry no `seedIndex` / `seedProductId` / `seedIsCaloParticle`
  but `caloParticleIdx` (the row of the SimTrackster's CaloParticle, i.e. of
  its `ticlSimTrackstersFromCPs` entry, remapped by the split; -1 when upstream
  has none or when the CaloParticle's own entry lies in the other endcap) and `isPU`; the trackster PCA shape (`EV1..3`, `eVector0_*`)
  and the LayerCluster `layer`, `algoID`, `correctedEnergy`, `time`,
  `timeError` are added.

A 100-event file converts in ≈ 1 min (2.6 GB RSS for one 100-event chunk) into
≈ 350 MB of split parquet, ≈ 3.5 MB per input event: the PU-200 LayerClusters
dominate. The nanoML output is unchanged (byte-identical to the previous version).

```shell
# one file by hand (apptainer image or LCG python as for nanoML files; bind /eos/cms into the container)
python3 root_to_parquet-split.py --nanoMLfiles /eos/cms/.../step3_inNANOAODSIM/step3_inNANOAODSIM_1440986_0.root \
    --outputDir out --outputFile step3_inNANOAODSIM_1440986_0_split.parquet
# the whole folder on condor, one parquet per file (see the parquet DAG below)
condor/submit_parquet.sh --indir /eos/cms/.../SinglePiPt25Eta1p7_2p7_D121/step3_inNANOAODSIM --pattern 'step3_inNANOAODSIM_*.root' \
    --outdir root://cmsdata.phys.cmu.edu//store/group/hgcal-ml-reconstruction/training_datasets/<dataset> --workdir ~/condor/<dataset>/parquet
```

## One-shot pipeline scripts

For a quick single-particle-type campaign, [`scripts/`](scripts/)
contains ready-made scripts that run the whole chain
(optional MINBIAS → GSD → RECO → nanoML → parquet) in one command on a
single machine. [`scripts/generate.sh`](scripts/generate.sh) takes the
particle as an option (`--particle <pdgId>`, plus `--fine-calo` for the
fine calorimeter truth); the per-particle wrappers below are shortcuts
that preset it:

|                | `useFineCalo=1`                                                                          | `useFineCalo=0`                                                                              |
| -------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| tau (`15`)     | [`generate_taus_FineCalo.sh`](scripts/generate_taus_FineCalo.sh)                           | [`generate_taus_noFineCalo.sh`](scripts/generate_taus_noFineCalo.sh)                           |
| photon (`22`)  | [`generate_photons_FineCalo.sh`](scripts/generate_photons_FineCalo.sh)                     | [`generate_photons_noFineCalo.sh`](scripts/generate_photons_noFineCalo.sh)                     |
| pi+ (`211`)    | —                                                                                        | [`generate_pions_noFineCalo.sh`](scripts/generate_pions_noFineCalo.sh)                         |
| K0L (`130`)    | —                                                                                        | [`generate_K0L_noFineCalo.sh`](scripts/generate_K0L_noFineCalo.sh)                             |

[`generate_taus_noFineCalo_30PU.sh`](scripts/generate_taus_noFineCalo_30PU.sh)
is the `noFineCalo` tau wrapper with `--pileup 30` preset: it runs the
`MINBIAS → GSD(PU) → RECO → nanoML → parquet` chain and tags its files
`taus_stdCalo_PU30_*`. A wrapper for another particle or pileup value is a
copy of the matching `noFineCalo` script with `PILEUP=<N>` added.

Pileup samples have no `MergedSimCluster` collection: pepr's
`SimClusterMerger` is not pileup-aware (see `runMergedSimClusters` in the
nanoML options above), so the chain runs nanoML with
`runMergedSimClusters=0` whenever `--pileup > 0`, and the parquet scripts
skip the group. `SimCluster`, the pileup-aware
`MergedCaloTruthMergedSimCluster`, CaloParticles, the TICL tracksters, the
SimTracksters and the reco↔sim associations are all written as usual, and
`*_isPileup` flags signal vs. pileup objects in the parquet.

All of them are thin wrappers around the shared driver
[`scripts/chain_common.sh`](scripts/chain_common.sh), which holds the
actual chain logic (argument parsing, step sequencing, logging, error
handling). `chain_common.sh` is sourced, not executed directly.

The `FineCalo` variants import `GSDfineCalo_fragment` (fine calorimeter
segmentation); the `noFineCalo` variants use `GSD_fragment` /
`GSD_fragment_PU` and are the **only** ones that support pileup, since
the fine-calo fragment has no PU-mixing wiring.

`cmsenv` must already be sourced. Defaults: 1000 events, `seed=1`,
gun energy flat in 20–200 GeV, `pileup=0`, 1 thread.

The parquet step does not use the CMSSW python (it has no `pyarrow`):
it runs `python3 root_to_parquet*.py` with `apptainer exec` inside the
`hgcal-offline-reco:3.12-latest` image from
`/cvmfs/unpacked.cern.ch/gitlab-registry.cern.ch/cms-cmu/hgcal-ml4reco/`,
binding `/afs`, `/cvmfs`, `/tmp`, `/eos/cms`, `/eos/user/<u>/$USER`, the repo
and the output directory and forwarding the kerberos ticket. Override the
image with `PARQUET_IMAGE=/path/to/image`, or bypass the container with
`PARQUET_PYTHON=/path/to/python3` (any python with uproot/awkward/pyarrow).

```shell
# Usage: bash <script> <output_directory> [n_events] [options]
bash scripts/generate_taus_FineCalo.sh      /path/to/output/folder 1000
bash scripts/generate_photons_FineCalo.sh   /path/to/output/folder 1000
bash scripts/generate_taus_noFineCalo.sh    /path/to/output/folder 1000
bash scripts/generate_photons_noFineCalo.sh /path/to/output/folder 1000

# With pileup: the minbias library is generated automatically with
# MINBIAS_GENSIM.py (seed = signal seed + 500000), unless --pu is given.
bash scripts/generate_taus_noFineCalo_30PU.sh /path/to/out 1000              # taus, 30 PU preset
bash scripts/generate_taus_noFineCalo_30PU.sh /path/to/out 10 --nminbias 100 # short test: library >= PU
bash scripts/generate_photons_noFineCalo.sh /path/to/out 500 --pileup 140 --nminbias 500

# Any particle: --particle sets the pdgId (file names use --partname, by
# default derived from it: photons, pions, K0L, taus, ... or pdg<N>)
bash scripts/generate.sh /path/to/out 1000 --particle 211

# Fixed energy with the closeBy gun (one particle on the +z HGCAL face, as in
# the earlier GNN energy-resolution samples)
bash scripts/generate.sh /path/to/out 1000 --particle 130 --gun closeBy --minE 10 --maxE 10

# Anything else: --help lists every option
bash scripts/generate_taus_FineCalo.sh --help
```

Options (all optional, after the two positional arguments):

| Option | Meaning |
| ------ | ------- |
| `--particle PDGID` | pdgId to shoot (required for `generate.sh`, preset by the wrappers) |
| `--partname NAME` | tag used in the file names (default derived from the pdgId) |
| `--fine-calo` | `useFineCalo=1` (fine calorimeter truth, no pileup support) |
| `--seed N` | random seed for the GSD step (default `1`) |
| `--minE X` / `--maxE X` | gun energy range in GeV (defaults `20.0` / `200.0`) |
| `--gun NAME` | `flatEta` (default) or `closeBy`, see the GSD options; `closeBy` leaves -z empty, so keep the default (non-split) parquet |
| `--nthreads N` | threads per `cmsRun` step (default `1`) |
| `--pileup N` | average pileup (default `0`, `30` in `generate_taus_noFineCalo_30PU.sh`); `noFineCalo` scripts only |
| `--pu FILE` | mix an existing minbias GEN-SIM file instead of generating one |
| `--nminbias N` | events for the auto-generated minbias library (default: `n_events`) |
| `--run-pftruth` | enable the nanoML PFTruth sequence (broken with pileup) |
| `--split` | use `root_to_parquet-split.py` (one row per HGCAL endcap) |
| `--no-parquet` | stop after the nanoML step |
| `--resume` | skip any step whose output file already exists (a failed step deletes its partial output, so it is rerun) |
| `--dry-run` | print the commands that would run, execute nothing |

Each run writes `<tag>_GSD.root`, `<tag>_RECO.root`, `<tag>_DQM.root`,
`<tag>_nanoML.root` and `<tag>.parquet` into the output directory
(plus `<tag>_MINBIAS.root` when pileup is generated), where `<tag>` is
`<partname>_{fineCalo,stdCalo}[_PU<N>]` — so the four configurations can
safely share one output directory. Per-step logs go to
`<output_directory>/logs/`. Any failing step prints `❌ ...`, names its
log file, and exits with status 1 without running the remaining steps.

The parquet conversion step needs `uproot`/`awkward`/`pyarrow`, which
the CMSSW python from `cmsenv` does not provide. The scripts default
to plain `python3` for that step; if yours doesn't have those
packages, point at one that does:

```shell
export PARQUET_PYTHON=/path/to/conda/envs/<env>/bin/python3
```

### Batch submission of the one-shot scripts (LPC)

[`condor/submit_oneshot.sh`](condor/submit_oneshot.sh) runs the one-shot
chain as LPC HTCondor jobs, one per (particle, energy, batch). Each job
([`condor/run_oneshot.sh`](condor/run_oneshot.sh)) unpacks the CMSSW
tarball, runs `scripts/generate.sh` for its batch and copies everything it
writes (GSD, RECO, nanoML, parquet, logs) to
`<outdir>/<partname>_E<E>/batch<b>/` on EOS, even when a step fails. Batch
`b` of pdgId `P` at energy `E` uses seed `P*100000 + E*1000 + seed_offset + b`
(`--seed-offset`, default 10). The parquet step runs with the LCG_109
python (`PARQUET_PYTHON`) because the apptainer image is not used in batch
jobs. Options after `--` go to every `generate.sh` call.

```shell
# From a shell without cmsenv, with a VOMS proxy. Defaults: photons (22),
# pions (211), K0L (130); 2-300 GeV; 4 batches x 250 events per energy.
bash condor/submit_oneshot.sh \
    --outdir /store/user/$USER/production_tests/pre4_D128/closeBy_Escan_oneshot \
    --workdir /uscms_data/d1/$USER/condor_work/closeBy_Escan_oneshot -- --gun closeBy

# --dry-run writes the submit file and job list without submitting
```
