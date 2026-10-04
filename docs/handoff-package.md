# Enhancement Handoff Package

**State date:** 2026-10-04

**Repository:** `9thLevelSoftware/critter-crafter`

**Purpose:** Resume the v0.3.0 enhancement work on a larger workstation without mistaking verified integration for release acceptance.

## Start here

1. Check out the merged `main` revision containing this handoff and read [handoff.md](handoff.md), [roadmap.md](roadmap.md), [locomotion.md](locomotion.md), and [technology-review.md](technology-review.md).
2. Set up Python with `uv sync`. Use Blender 5.2.x for authoring/deformation work and the pinned Unity 6000.6.0f1 editor for Unity checks. Follow `AGENTS.md` for the complete test and build commands.
3. Confirm the working tree and tool versions before resuming. Do not assume owner approval, successful functional search, or model output from the passing integration tests.

## Verified integration state

- `critter schema validate`: zero diagnostics. Recipe golden generation produced 1,200 recipe, 468 locomotion, and 211 creature rows; all four v3 Python/Unity golden copies matched byte-for-byte.
- Library build: 1,517 assets and 91 assemblies. All 91 skeletons passed eight-clip motion/action/export QA; all nine S/M/L native-SDF connector variants passed the unchanged deformation QA. All skeletons, parts, and connectors remain draft pending owner approval.
- Python/Blender full suite: 674 passed, one skipped. Unity EditMode: 114 passed, zero failed, one skipped because `CRITTER_PROTOTYPE_FBX` and `CRITTER_PROTOTYPE_CATALOG` were not configured.
- Native Unity bake proof: `work/review/creature-bake-unity-resume-final/proof.json`. A non-batch Unity GUI run on RTX 4070 Laptop / Direct3D12 measured 15 live versus one baked body submission in each color, shadow, and depth pass; eight actions and 14 locomotion courses were captured. Portable 4096² atlas comparison retained 32 px gutters and measured zero pose error over three samples of each action. This is proof of the bake path, not M4 acceptance or owner visual approval.

These are observed results recorded by the prior run; they are not a promise that another workstation has the same local generated artifacts. Re-run affected acceptance checks after changing code or inputs.

## Unaccepted / blocked gates

- **Morphology functional search:** the seed-2, 17-proposal run had zero feasible elites (`CC_SEARCH_NO_FEASIBLE`); there is no candidate hash eligible for draft export. Investigate feasibility and candidate generation; never create a neutral duplicate or bypass QA to satisfy export.
- **Contact proposals:** paired real Play Mode measurements cover 15 review rows, 15 turn rows, and five stationary reaction rows. The no-regression gate fails (nine review and eight turn maxima worsen). Reactions had no regression. Targeted traces show the walker lift refusal at the existing minimum-support floor and the belly-hauler reach peak after a center landing. No gate-preserving causal correction is established. Preserve the existing score, support, reach, and yaw rules.
- **Learned skin:** no actual inference/import/deformation QA has run. Requires an owner-managed CUDA worker with at least 16 GB VRAM; SkinTokens and UniRig checkouts at the revisions enforced in `tools/authoring_worker/skin.py`; and the official SkinTokens TokenRig and FSQ-CVAE checkpoints matching the hashes enforced there. SkinTokens uses a separate Python 3.11 / PyTorch 2.7.0 / CUDA 12.8 environment. Set `CRITTER_SKINTOKENS_ROOT`; set `CRITTER_UNIRIG_ROOT` only for the comparison backend. Checkpoint/model files are not included in this repository.
- **Owned-motion models:** no actual model training/inference/import/action/export QA has run. Requires the owner-managed >=16 GB CUDA worker, a clean AnyTop checkout in its separate Python 3.8.15 / PyTorch 2.4.1 / CUDA 12.1 environment, and `CRITTER_ANYTOP_ROOT`. Use only the project-authored, rights-cleared motion dataset; do not use upstream Truebones downloads or pretrained motion checkpoints. The worker path performs five separate action-model training runs; budget GPU time and storage accordingly.
- **M4 / owner review:** 24 approved parts, accepted functional search, and owner review/publication remain outstanding. Asset approvals are owner-only. Do not commit private source meshes, textures, renders, previews, or review sheets.
- **M5 consumer integration:** remains gated on the owner's intended gameplay checkout/revision containing its real spawn, view/factory, movement/event, and save implementations. Do not wire against guessed APIs.

## Suggested resume sequence

1. Recreate the baseline on the new machine: `uv sync`, then `uv run critter schema validate` and `uv run pytest -m "not blender"`. Use the repository's full verification pipeline before accepting changes.
2. Continue morphology feasibility/search first; inspect `uv run critter skeleton search --help` and the implementation/docs before launching a new run. Export only a feasible, novel candidate with its actual search hash, as draft.
3. Set up the owner-provided GPU worker environments and authorized checkpoints. Run the real skin and motion prepare → inference/train → import → QA flows; keep each backend isolated and preserve its revision/checkpoint hashes.
4. Revisit contact proposals only with a measured candidate-level counterfactual that preserves existing rules and passes the complete paired no-regression gate.
5. Submit visual/asset choices to the owner. Do not promote drafts or claim M4/M5 acceptance until their explicit gates pass.

## Artifacts not carried by Git

Generated build libraries, captures, test logs, review images, and model/checkpoint files remain outside Git by design. The local workstation also contains untracked generated candidate packages under `k/7cb9be7b2e7b/` and `k/5dd7f5cb4fbb/`, including rendered captures and compiled libraries. The untracked Unity `PackageManagerSettings.asset` is local editor state. These files were deliberately not added to the remote repository. If those exact local files are needed on the larger workstation, transfer them through the owner's approved private channel; otherwise regenerate outputs from the committed sources. The targeted contact traces are in `work/enhancements/proposals-contact-diagnostics-walker.log` and `work/enhancements/proposals-contact-diagnostics-belly.log` on the original workstation.
