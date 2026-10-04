"""Interchange semantics, topology-held-out splits and bone-only dataset contracts."""
from __future__ import annotations

import math

import pytest

from critter_crafter.authoring.motion_dataset import (
    decode_frames, encode_frames, from_rotation6, lineage_split, multiply,
    quaternion_matrix, recovered_heads, role_vectors, rotation6, transpose,
)

IDENTITY = [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]


def test_world_columns_preserve_heading_and_bind_relative_asymmetry():
    root_bind = quaternion_matrix([0., math.sin(.3), 0., math.cos(.3)])
    child_bind = quaternion_matrix([math.sin(.2), 0., 0., math.cos(.2)])
    root_delta = [0.,0.,math.sin(.15),math.cos(.15)]
    child_delta = [0.,math.sin(.25),0.,math.cos(.25)]
    root_world = multiply(root_bind,quaternion_matrix(root_delta))
    child_world = multiply(multiply(root_world,multiply(transpose(root_bind),child_bind)),quaternion_matrix(child_delta))
    record = {"bone_names":["root","asymmetric_tip"],"parents":[-1,0],"body_height_m":2.,
              "bind_rotations":[root_bind,child_bind],"bind_heads_m":[[0.,0.,0.],[.7,1.1,-.2]],
              "root_bind_head_m":[0.,0.,0.],"root_positions_m":[[0.,.1,0.]]}
    expected = {"rotations_xyzw":{"root":root_delta,"asymmetric_tip":child_delta},"root_position_m":[0.,.1,0.]}
    heads = recovered_heads(expected,record)
    sample = {"root_position_m":[0.,.1,0.],"bones":[
        {"name":"root","head_m":heads["root"],"world_rotation":root_world},
        {"name":"asymmetric_tip","head_m":heads["asymmetric_tip"],"world_rotation":child_world}],
        "contacts":[{"contact_id":"socket:0","planted":True}]}
    features = encode_frames([sample],record["bone_names"],2.,{"socket:0":"asymmetric_tip"})
    assert features[0][1][12] == 1. and features[0][0][12] == 0.
    assert features[0][1][3:9] == pytest.approx(rotation6(child_world))
    decoded = decode_frames(features,record)[0]
    for name in record["bone_names"]:
        assert decoded["rotations_xyzw"][name] == pytest.approx(expected["rotations_xyzw"][name],abs=1e-10)
        assert recovered_heads(decoded,record)[name] == pytest.approx(heads[name],abs=1e-10)


def test_velocity_is_backward_difference_in_body_heights_per_second():
    def sample(y):
        return {"root_position_m":[0.,y,0.],"bones":[{"name":"root","head_m":[0.,y,0.],"world_rotation":IDENTITY}],"contacts":[]}
    features = encode_frames([sample(.2),sample(.3)],["root"],2.,{})
    assert features[0][0][9:12] == [0.,0.,0.]
    assert features[1][0][9:12] == pytest.approx([0.,1.5,0.])


@pytest.mark.parametrize("values",[[0.]*6,[1.,0.,0.,2.,0.,0.]])
def test_degenerate_rotation6_is_rejected(values):
    with pytest.raises(ValueError,match="CC_MOTION_ROTATION"):
        from_rotation6(values)


@pytest.mark.parametrize("archetype,split",[("radial_raised_walker","held_out"),("serpentine_segmented_limbed","held_out"),
                                             ("serpentine_limbless","held_out"),("biped_plantigrade_humanoid","train")])
def test_complete_curated_lineages_are_held_out(archetype,split):
    for variant in ("compact","balanced","tall"):
        skeleton = {"skeleton_id":archetype+variant,"anatomy":{"archetype_id":archetype}}
        assert lineage_split(skeleton) == (split,archetype)


def test_amalgam_variants_cannot_cross_seed_split():
    for seed in (24,25):
        for mode in ("hauled","walker","slither"):
            skeleton = {"skeleton_id":f"amalgam_{mode}_mhash_v3","anatomy":{"archetype_id":"amalgam_"+mode},
                        "provenance":{"seed":seed}}
            assert lineage_split(skeleton) == ("held_out" if seed == 25 else "train","amalgam:"+str(seed))


def test_capability_embeddings_do_not_depend_on_bone_names():
    skeleton = {"branches":[{"bone_names":["renamed"],"capabilities":["support","strike","grasp"],"gait_role":"puller"}]}
    root,limb = role_vectors(skeleton,["root","renamed"])
    assert root[6] == 1.
    assert limb[:13] == [1.,1.,0.,0.,1.,0.,0.,0.,0.,0.,1.,0.,0.]
    assert all(v == 0. for v in limb[13:])


def test_float_roundoff_in_bind_basis_does_not_accumulate_down_an_axial_chain():
    from critter_crafter.authoring.motion_dataset import make_record
    names = ["root"] + [f"axial_{index}" for index in range(8)]
    noisy_bind = [[1.,0.,0.],[0.,0.,1.],[0.,-1.,2.4e-6]]
    source_bones = [{"name":"root","parent":"","head_m":[0.,0.,0.]}] + [
        {"name":name,"parent":names[index],"head_m":[0.,0.,-.3*index]}
        for index,name in enumerate(names[1:])]
    evaluated = [{"name":bone["name"],"head_m":bone["head_m"],
                  "rotation_xyzw":[0.,0.,0.,1.],
                  "world_rotation":IDENTITY if bone["name"] == "root" else noisy_bind}
                 for bone in source_bones]
    samples = [{"frame":frame,"root_position_m":[0.,0.,0.],"bones":evaluated,
                "contacts":[{"contact_id":"axis:0","planted":True}]} for frame in range(2)]
    skeleton = {"skeleton_id":"public_axial_fixture","bones":source_bones,
                "branches":[{"branch_id":"axis","bone_names":names[1:],"contacts":[{"bone_index":7}],
                             "capabilities":["support","slide"],"gait_role":"slither"}],
                "anatomy":{"archetype_id":"serpentine_fixture","support_branches":["axis"],
                           "silhouette":{"height_m":1.}}}
    artifact = {"sample_source":"evaluated_blender","fps":30,"skeleton_snapshot":skeleton,
                "source_fingerprint":"fixture","contact_bones":{"axis:0":names[-1]},
                "bind_rotations":{name:IDENTITY if name == "root" else noisy_bind for name in names},
                "neutral_world_sample":samples[0],"clips":[{"name":"idle","samples":samples}]}
    record = make_record(artifact,"idle")
    for restored in decode_frames(record["features"],record):
        heads = recovered_heads(restored,record)
        for bone in source_bones:
            assert heads[bone["name"]] == pytest.approx(bone["head_m"],abs=1e-10)


def test_world_rotation_encoding_preserves_authoritative_evaluated_local_delta():
    from critter_crafter.authoring.motion_dataset import make_record
    authoritative = [0.2774023,0.0007501,-0.0026127,0.96075]
    independently_orthogonalized = [0.2773922141911527,0.0007501356278532351,-0.002612740602133344,0.9607529184911257]
    bones = [{"name":"root","parent":"","head_m":[0.,0.,0.]},
             {"name":"effector","parent":"root","head_m":[.2,.3,-.1]}]
    evaluated = [{"name":"root","head_m":[0.,0.,0.],"rotation_xyzw":[0.,0.,0.,1.],"world_rotation":IDENTITY},
                 {"name":"effector","head_m":[.2,.3,-.1],"rotation_xyzw":authoritative,
                  "world_rotation":quaternion_matrix(independently_orthogonalized)}]
    samples = [{"frame":frame,"root_position_m":[0.,0.,0.],"bones":evaluated,
                "contacts":[{"contact_id":"limb:0","planted":True}]} for frame in range(2)]
    skeleton = {"skeleton_id":"public_evaluated_delta_fixture","bones":bones,
                "branches":[{"branch_id":"limb","bone_names":["effector"],"contacts":[{"bone_index":0}],
                             "capabilities":["support","strike"],"gait_role":"manipulator"}],
                "anatomy":{"archetype_id":"biped_fixture","support_branches":["limb"],
                           "silhouette":{"height_m":1.}}}
    artifact = {"sample_source":"evaluated_blender","fps":30,"skeleton_snapshot":skeleton,
                "source_fingerprint":"fixture","contact_bones":{"limb:0":"effector"},
                "bind_rotations":{"root":IDENTITY,"effector":IDENTITY},
                "neutral_world_sample":samples[0],"clips":[{"name":"idle","samples":samples}]}
    record = make_record(artifact,"idle")
    for restored in decode_frames(record["features"],record):
        assert restored["rotations_xyzw"]["effector"] == pytest.approx(authoritative,abs=2e-7)
        assert recovered_heads(restored,record)["effector"] == pytest.approx([.2,.3,-.1],abs=1e-10)
