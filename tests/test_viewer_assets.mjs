/* Actual built-asset integration for the same local Three/GLTFLoader runtime used by the viewer. */
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import * as THREE from '../src/critter_crafter/skeletons/viewer/vendor/three.module.js';
import { GLTFLoader } from '../src/critter_crafter/skeletons/viewer/vendor/GLTFLoader.js';
import { neutralLocalQuaternion, rootLocalOffset } from '../src/critter_crafter/skeletons/viewer/pose-math.mjs';

const root = resolve('library');
const expectedClips = ['idle', 'walk', 'run', 'stun', 'telegraph', 'attack', 'hit', 'death'];

async function latestBuiltLibrary() {
  const candidates = [];
  for (const name of await readdir(root)) {
    try {
      const directory = join(root, name), catalog = JSON.parse(await readFile(join(directory, 'catalog.json'), 'utf8'));
      if (catalog.schema_version === '3.0.0') candidates.push({ directory, catalog });
    } catch { /* Non-library entries are irrelevant. */ }
  }
  return candidates.sort((a, b) => a.directory.localeCompare(b.directory)).at(-1);
}

async function parseGlb(path) {
  const data = await readFile(path);
  return new Promise((resolveParse, rejectParse) => new GLTFLoader().parse(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength), '', resolveParse, rejectParse));
}

function bones(scene) {
  const values = new Map();
  scene.traverse((node) => { if (node.isBone) values.set(node.name, node); });
  return values;
}

function close(a, b, epsilon = 3e-4) { return a.distanceTo(b) <= epsilon; }

const built = await latestBuiltLibrary();
if (!built) {
  console.log('SKIP viewer asset integration: no v3 built library');
  process.exit(0);
}

const preferred = ['biped_plantigrade_humanoid_balanced_v3', 'radial_low_tentacle_crawler_balanced_v3', 'serpentine_limbless_articulated_balanced_v3'];
const selections = preferred.map((id) => built.catalog.skeletons.find((s) => s.skeleton_id === id)).filter(Boolean);
assert.ok(selections.length >= 1, 'built catalog includes a representative viewer skeleton');

for (const skeleton of selections) {
  const gltf = await parseGlb(join(built.directory, skeleton.asset.glb));
  const motion = JSON.parse(await readFile(join(built.directory, skeleton.asset.motion), 'utf8'));
  assert.deepEqual(gltf.animations.map((clip) => clip.name).sort(), [...expectedClips].sort(), `${skeleton.skeleton_id}: GLB exposes all eight clips`);
  assert.deepEqual(motion.clips.map((clip) => clip.name), expectedClips, `${skeleton.skeleton_id}: motion samples expose all eight clips`);

  const actualBones = bones(gltf.scene), bind = new Map(); gltf.scene.updateMatrixWorld(true); gltf.scene.traverse((node) => { if (node.isBone) { const world = node.getWorldQuaternion(new THREE.Quaternion()), worldPosition = node.getWorldPosition(new THREE.Vector3()); bind.set(node.name, { local: node.quaternion.clone(), position: node.position.clone(), world, worldPosition }); } });
  const clipPlan = motion.clips.find((clip) => clip.name === 'walk');
  const frame = Math.max(1, Math.floor(clipPlan.frames / 2));
  const mixer = new THREE.AnimationMixer(gltf.scene), action = mixer.clipAction(gltf.animations.find((clip) => clip.name === 'walk'));
  action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true; action.play(); mixer.setTime(frame / clipPlan.fps); gltf.scene.updateMatrixWorld(true);
  const sample = clipPlan.samples[frame];
  const expectedBone = sample.bones.find((bone) => actualBones.has(bone.name) && new THREE.Vector3(...bone.head_m).distanceTo(bind.get(bone.name).worldPosition) > 1e-5) || sample.bones.find((bone) => actualBones.has(bone.name));
  assert.ok(expectedBone, `${skeleton.skeleton_id}: sampled deformation bone exists in GLB`);
  const actualPosition = actualBones.get(expectedBone.name).getWorldPosition(new THREE.Vector3());
  assert.ok(close(actualPosition, new THREE.Vector3(...expectedBone.head_m)), `${skeleton.skeleton_id}: walk frame ${frame} bone position matches baked motion sample`);
  assert.ok(actualPosition.distanceTo(bind.get(expectedBone.name).worldPosition) > 1e-5, `${skeleton.skeleton_id}: nonzero walk frame moves a deformation bone`);

  mixer.stopAllAction(); gltf.scene.traverse((node) => { if (node.isSkinnedMesh) node.skeleton.pose(); }); gltf.scene.updateMatrixWorld(true);
  const bindPose = new Map(); gltf.scene.traverse((node) => { if (node.isBone) bindPose.set(node.name, { local: node.quaternion.clone(), position: node.position.clone() }); });
  mixer.setTime(frame / clipPlan.fps); gltf.scene.updateMatrixWorld(true);
  mixer.stopAllAction(); gltf.scene.traverse((node) => { if (node.isSkinnedMesh) node.skeleton.pose(); }); gltf.scene.updateMatrixWorld(true);
  for (const [name, value] of bindPose) {
    const node = actualBones.get(name); assert.ok(node.quaternion.angleTo(value.local) < 1e-3 && node.position.distanceTo(value.position) < 1e-6, `${skeleton.skeleton_id}: bind restores ${name}`);
  }

  const catalogBones = new Map(skeleton.bones.map((bone) => [bone.name, bone]));
  for (const rotation of skeleton.neutral_pose.rotations) {
    const node = actualBones.get(rotation.bone_name), catalogBone = catalogBones.get(rotation.bone_name), source = bind.get(rotation.bone_name);
    if (node && catalogBone && source) node.quaternion.copy(neutralLocalQuaternion(catalogBone, rotation.rotation_xyzw, source.world, source.local));
  }
  const rootBone = actualBones.get('root');
  if (rootBone) { const parentWorld = new THREE.Quaternion(); rootBone.parent.getWorldQuaternion(parentWorld); rootBone.position.copy(bind.get('root').position).add(rootLocalOffset(skeleton.neutral_pose.root_offset_m, parentWorld)); }
  gltf.scene.updateMatrixWorld(true);
  const nonIdentity = skeleton.neutral_pose.rotations.find((rotation) => rotation.rotation_xyzw.some((value, index) => (index === 3 ? Math.abs(value - 1) : Math.abs(value)) > 1e-6));
  if (nonIdentity) assert.ok(actualBones.get(nonIdentity.bone_name).quaternion.angleTo(bind.get(nonIdentity.bone_name).local) > 1e-5, `${skeleton.skeleton_id}: neutral helper applies authored source rotation`);
  assert.ok(rootBone.position.distanceTo(bind.get('root').position) > 1e-6 || skeleton.neutral_pose.root_offset_m.every((value) => value === 0), `${skeleton.skeleton_id}: neutral helper applies authored root offset`);
}
