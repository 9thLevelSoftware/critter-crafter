# Interactive workflows (Blender, Unity and Meshy MCPs)

The CLI is the source of truth; the MCP servers are for looking at things live and for
hand-tuning. Everything the CLI does in Blender is a pure `run(args) -> result` op function in
`src/critter_crafter/blender/ops_*.py`, and the same functions run inside a live Blender session.

## Blender MCP
* `uv run critter blender snippet <skeleton_or_part_id>` prints Python for the Blender MCP
  `execute_blender_code` tool. It rebuilds that skeleton (with all clips) or placeholder part in the open
  Blender scene, so you can scrub clips, inspect weights and try pose tweaks.
* Status: the snippet sets `rigkit.LIVE = True`, which builds into a new `critter_preview` scene and
  never factory-resets your open file. It has been run through headless Blender but **not yet against a
  live Blender MCP session**. Running it repeatedly in one session creates suffixed actions
  (`idle.001` and so on); open a fresh file between runs until that is handled.
* Masters from `critter library build` are saved to `work/masters/**/master.blend` for opening
  directly.
* Hand-polished clips (M1+) are saved back into the skeleton master; `critter library build` must not
  overwrite a skeleton whose status is `approved`.

## Unity MCP / Unity CLI
* The test bed is `unity/TestProject`. It imports `library/<id>-v<ver>` through
  `LibraryImporter.Import(dir)`.
* Review sheet via the game-style ortho iso camera:
  `-executeMethod CritterCrafter.Editor.ReviewCapture.CaptureFromCommandLine -critterLibrary <dir> -critterOut <png> -critterPool any -critterSeeds 1..8 -critterClip walk`

## Meshy MCP (M2/M3)
* **Always confirm the credit cost with the user before calling any paid tool.**
* Use text-to-3d, not image-to-3d, for organic shapes. Prompts come from
  `data/prompt_profiles/flesh_stylized_v1.json`. Elongated parts get "straight extended, NOT coiled".
* Downloads have no file extension. Store them in `synaptic-sea-asset-archive` (never in this public
  repository), then fit one onto a profile with `critter part import <archive path> --part-id ...
  --profile ... --axis ...`. It detects glTF from the file's first bytes. See [parts.md](parts.md).
