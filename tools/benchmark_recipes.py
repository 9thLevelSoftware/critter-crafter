"""Offline Phase-1 recipe proxy benchmark; never evidence of geometry or runtime quality."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.locomotion.build import creature_locomotion
from critter_crafter.locomotion.qa import evaluate_locomotion
from critter_crafter.recipes.generator import canonical, generate
from critter_crafter.recipes.validate import validate_recipe

POOLS = (
    "any", "biped", "quadruped", "crawler", "hexapod", "radial",
    "serpentine", "dragger", "puppet_corpse", "stalker", "biomatter_swarm", "amalgam",
)
SEEDS = range(1, 101)


def normalized_json(document: Any) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def run_pass(
    catalog: dict[str, Any],
    pass_number: int,
    baseline: list[bytes] | None = None,
) -> tuple[list[bytes], list[int], int, int, int, str]:
    outputs: list[bytes] = []
    diversity: list[int] = []
    valid = 0
    invalid = 0
    mismatches = 0
    digest = hashlib.sha256()
    for pool_id in POOLS:
        forms: set[str] = set()
        pool_invalid = 0
        for seed in SEEDS:
            recipe = generate(catalog, pool_id, seed)
            recipe_diags = validate_recipe(catalog, recipe)
            locomotion = creature_locomotion(catalog, recipe)
            locomotion_diags = evaluate_locomotion({"locomotion": locomotion})
            if recipe_diags or locomotion_diags:
                pool_invalid += 1
                for diagnostic in recipe_diags:
                    print(f"pass={pass_number} pool={pool_id} seed={seed}: {diagnostic}", file=sys.stderr)
                for diagnostic in locomotion_diags:
                    print(
                        f"pass={pass_number} pool={pool_id} seed={seed}: "
                        f"{diagnostic['code']} {diagnostic['detail']}",
                        file=sys.stderr,
                    )
            else:
                valid += 1
                forms.add(canonical(recipe))
            serialized = normalized_json({
                "pool_id": pool_id, "seed": seed, "recipe": recipe, "locomotion": locomotion,
            })
            if baseline is not None and serialized != baseline[len(outputs)]:
                mismatches += 1
                print(f"replay mismatch pool={pool_id} seed={seed}", file=sys.stderr)
            outputs.append(serialized)
            digest.update(serialized)
            digest.update(b"\n")
        diversity.append(len(forms))
        invalid += pool_invalid
        print(
            f"pass={pass_number} pool={pool_id:16s} seeds={len(SEEDS)} "
            f"distinct={len(forms)} invalid={pool_invalid}"
        )
    return outputs, diversity, valid, invalid, mismatches, digest.hexdigest()


def main() -> int:
    print("Phase-1 offline deterministic recipe benchmark")
    print(f"Workload: pools={','.join(POOLS)}; seeds=1..100; passes=2; attempts_per_pass=1200")
    print("Approval policy: draft skeletons approved only in memory; rejected skeletons and all parts unchanged.")
    print("Diversity: canonical skeleton/branch part/connector IDs; scales excluded; invalid recipes excluded.")
    print("Scope: recipe-level proxy only; not geometry, asset QA, runtime, visual, or new-technology proof.")
    print("Validation failures are measured outcomes; replay disagreement or execution errors fail the harness.")
    catalog = compile_catalog(load_sources(Path(__file__).resolve().parents[1] / "data"))
    print(f"source_catalog_sha256={hashlib.sha256(normalized_json(catalog)).hexdigest()}")
    for skeleton in catalog["skeletons"]:
        if skeleton["status"] == "draft":
            skeleton["status"] = "approved"

    first, diversity, valid, first_invalid, _, first_hash = run_pass(catalog, 1)
    _, _, _, replay_invalid, mismatches, replay_hash = run_pass(catalog, 2, first)
    print("Output hash encoding: ordered normalized JSON rows (full recipe + locomotion), each followed by LF.")
    print(f"generated_outputs_sha256={first_hash}")
    print(f"replay_outputs_sha256={replay_hash}")
    if mismatches or first_invalid != replay_invalid:
        print(
            f"FAILED: first_pass_invalid={first_invalid} replay_invalid={replay_invalid} "
            f"replay_mismatches={mismatches}",
            file=sys.stderr,
        )
        return 1

    print(f"METRIC min_pool_distinct={min(diversity)}")
    print(f"METRIC mean_pool_distinct={sum(diversity) / len(diversity):.6f}")
    print(f"METRIC valid_recipes={valid}")
    print(f"METRIC invalid_recipes={first_invalid}")
    print("METRIC replay_mismatches=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
