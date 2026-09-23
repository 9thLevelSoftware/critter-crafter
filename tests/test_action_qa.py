import copy
import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.skeletons.actions import ATTACK_PROFILES, attack_target_at, resolve_attack
from critter_crafter.skeletons.action_qa import evaluate_actions, neutral_effector_anchor


@pytest.fixture(scope="module")
def catalog():
    return compile_catalog(load_sources(paths().data))


def samples_for(skeleton):
    plan = resolve_attack(skeleton)
    anchor = neutral_effector_anchor(skeleton, plan)
    clips = []
    for name in ("telegraph", "attack"):
        samples = []
        for frame in range(19):
            w = plan["timing"]["windup_end"]
            phase = frame / 18 * w if name == "telegraph" else w + frame / 18 * (1-w)
            target = attack_target_at(plan, phase)
            contacts = [{"contact_id": f"{branch['branch_id']}:{index}", "branch_id": branch["branch_id"],
                         "planted": f"{branch['branch_id']}:{index}" not in target["release_contact_ids"]}
                        for branch in skeleton["branches"] for index, _ in enumerate(branch.get("contacts", []))]
            samples.append({"frame": frame, "contacts": contacts,
                            "bones": [{"name": plan["effector"]["bone_name"],
                                       "tail_m": [a+b for a,b in zip(anchor, target["target_offset_m"])]}]})
        clips.append({"name": name, "frames": 18, "samples": samples})
    return {"attack_plan": plan, "attack_anchor_m": anchor, "clips": clips}


@pytest.mark.parametrize("archetype", sorted(ATTACK_PROFILES))
def test_declared_trajectory_and_support_are_verified_for_every_archetype(catalog, archetype):
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == archetype+"_balanced_v3")
    motion = samples_for(skeleton)
    assert evaluate_actions(skeleton, motion)["passed"]
    stationary = copy.deepcopy(motion)
    for sample in stationary["clips"][1]["samples"]:
        sample["bones"][0]["tail_m"] = motion["clips"][1]["samples"][0]["bones"][0]["tail_m"]
    codes = {d["code"] for d in evaluate_actions(skeleton, stationary)["diagnostics"]}
    assert {"CC_ACTION_TARGET", "CC_ACTION_DISPLACEMENT"} <= codes


def test_targets_and_anchor_cannot_be_relabelled_to_certify_bad_motion(catalog):
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    motion = samples_for(skeleton)
    motion["attack_plan"]["trajectory"]["impact_offset_m"][0] += 1
    motion["attack_anchor_m"][1] += 1
    motion["clips"][1]["samples"][5]["contacts"][0]["planted"] = False
    codes = {d["code"] for d in evaluate_actions(skeleton, motion)["diagnostics"]}
    assert {"CC_ACTION_PROVENANCE", "CC_ACTION_ANCHOR", "CC_ACTION_SUPPORT"} <= codes


def test_effector_must_replant_after_recovery(catalog):
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    motion = samples_for(skeleton)
    contact = next(c for c in motion["clips"][1]["samples"][-1]["contacts"] if c["contact_id"] == "leg_2:0")
    contact["planted"] = False
    assert "CC_ACTION_SUPPORT" in {d["code"] for d in evaluate_actions(skeleton, motion)["diagnostics"]}


@pytest.mark.parametrize("anchor", [None, [float("nan"), 0, 0], [0, 0], [0, 0, "invalid"]])
def test_baked_anchor_requires_a_finite_three_component_vector(catalog, anchor):
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    motion = samples_for(skeleton)
    if anchor is None:
        del motion["attack_anchor_m"]
    else:
        motion["attack_anchor_m"] = anchor
    result = evaluate_actions(skeleton, motion)
    assert not result["passed"]
    assert "CC_ACTION_ANCHOR" in {d["code"] for d in result["diagnostics"]}
