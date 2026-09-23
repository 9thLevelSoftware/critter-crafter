import * as THREE from './vendor/three.module.js';
import { GLTFLoader } from './vendor/GLTFLoader.js';
import { neutralLocalQuaternion, rootLocalOffset } from './pose-math.mjs';
import { advanceTime, clampTime, frameAt, selectedClipTime, timelineLabel } from './timeline.mjs';
import { bindTimelineControls, createTimelineActions } from './controls.mjs';

const $ = (id) => document.getElementById(id);
const canvas = $('canvas'), loading = $('loading');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x141618);
const camera = new THREE.PerspectiveCamera(42, 1, .01, 1000);
const ambient = new THREE.HemisphereLight(0xe8eef0, 0x2a3032, 2.2); scene.add(ambient);
const key = new THREE.DirectionalLight(0xfff4df, 2.5); key.position.set(4, 7, 5); scene.add(key);
const grid = new THREE.GridHelper(20, 40, 0x59656b, 0x2d3438); scene.add(grid);
const loader = new GLTFLoader();
const root = new THREE.Group(); scene.add(root);
const state = { manifest: null, skeleton: null, gltf: null, assembled: null, subject: null, mixer: null,
  skeletonMixer: null, assembledMixer: null, action: null, contacts: null, playing: true, travel: true,
  mode: 'bones', bounds: null, clip: null, pose: 'animation', currentTime: 0, playbackRate: 1, yaw: .58, pitch: .25, radius: 4,
  bindWorld: new Map(), bindLocal: new Map(), bindPosition: new Map() };

function rel(path) { return `./${path}`; }
function selectedSkeleton() { return state.manifest.skeletons.find((s) => s.skeleton_id === $('skeleton').value); }
function dataRows(id, values) { $(id).replaceChildren(...Object.entries(values).map(([k, v]) => {
  const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = k; dd.textContent = typeof v === 'object' ? JSON.stringify(v) : String(v); return [dt, dd];
}).flat()); }
function clipMetadata() {
  const catalogClip = state.skeleton.asset.clips.find((c) => c.name === state.clip) || {};
  const sampledClip = state.motion.clips.find((c) => c.name === state.clip) || {};
  const fps = sampledClip.fps || catalogClip.fps || 30;
  return { name: state.clip, frames: sampledClip.frames ?? catalogClip.frames ?? 0, fps,
    timing: `${sampledClip.frames || catalogClip.frames || '?'} frames @ ${fps} fps`,
    duration_s: sampledClip.duration_s ?? ((catalogClip.frames || 0) / fps),
    speed_mps: sampledClip.speed_mps ?? catalogClip.speed_mps ?? catalogClip.nominal_speed_mps ?? 0,
    samples: sampledClip.samples?.length || 0, loop: sampledClip.loop ?? catalogClip.loop ?? false };
}
function updateTimeline() {
  const meta = clipMetadata(), duration = Number(meta.duration_s) || 0, fps = Number(meta.fps) || 30;
  $('scrub').max = String(duration); $('scrub').step = String(1 / fps); $('scrub').value = String(clampTime(state.currentTime, duration));
  $('time').textContent = timelineLabel(state.pose, frameAt(state.currentTime, fps, Number(meta.frames) || Math.round(duration * fps)), state.currentTime, duration);
}
function setClipTime(time) {
  const meta = clipMetadata(), duration = Number(meta.duration_s) || 0;
  state.currentTime = clampTime(time, duration);
  if (state.action) { state.action.enabled = true; state.action.time = state.currentTime; }
  state.mixer?.update(0); updateContacts(state.currentTime);
  if (state.subject) state.subject.position.z = state.travel ? (Number(meta.speed_mps) || 0) * state.currentTime : 0;
  updateTimeline();
}
function updateMeta() {
  const s = state.skeleton; const c = clipMetadata();
  dataRows('clipInfo', c); dataRows('provenance', { id: s.skeleton_id, family: s.family, status: s.status,
    source_fingerprint: s.source_fingerprint, content_fingerprint: s.content_fingerprint, provenance: s.provenance });
  const assembly = s.asset.assembled_glb ? 'Available: built assembled GLB.' : 'Unavailable: no built compatible assembled GLB was supplied.';
  $('availability').textContent = state.mode === 'assembled' ? assembly : 'Displaying built skeleton GLB and its baked motion.json samples.';
}
function clearObject(object) { if (object) root.remove(object); }
function skeletonHelper(object) { const h = new THREE.SkeletonHelper(object); h.material = new THREE.LineBasicMaterial({ color: 0xdac589, depthTest: false, depthWrite: false }); h.renderOrder = 10; return h; }
function makeContacts() {
  const group = new THREE.Group(); group.name = 'baked-contacts'; state.contacts = new Map();
  const samples = state.motion.clips.find((c) => c.name === state.clip)?.samples || [];
  for (const item of samples.flatMap((sample) => sample.contacts || [])) {
    const contactId = item.contact_id || item.branch_id; if (state.contacts.has(contactId)) continue;
    const marker = new THREE.Mesh(new THREE.SphereGeometry(.035, 10, 8), new THREE.MeshBasicMaterial({ color: item.planted ? 0x78c995 : 0xe68f5c })); marker.name = `contact:${item.branch_id}`;
    const target = new THREE.Mesh(new THREE.SphereGeometry(.022, 10, 8), new THREE.MeshBasicMaterial({ color: 0x82b8e8 })); target.name = `target:${item.branch_id}`;
    state.contacts.set(contactId, { marker, target }); group.add(marker, target);
  }
  updateContacts(0); return group;
}
function updateContacts(time) {
  if (!state.contacts) return; const clip = state.motion.clips.find((c) => c.name === state.clip); if (!clip?.samples?.length) return;
  for (const points of state.contacts.values()) { points.marker.visible = false; points.target.visible = false; }
  const index = Math.min(Math.round(time * (clip.fps || 30)), clip.samples.length - 1);
  for (const item of clip.samples[index].contacts || []) { const points = state.contacts.get(item.contact_id || item.branch_id); if (!points || !Array.isArray(item.position_m)) continue; points.marker.visible = true; points.marker.position.fromArray(item.position_m); points.marker.material.color.setHex(item.planted ? 0x78c995 : 0xe68f5c); if (Array.isArray(item.target_m)) { points.target.visible = true; points.target.position.fromArray(item.target_m); } }
}
function hideContacts() { for (const points of state.contacts?.values() || []) { points.marker.visible = false; points.target.visible = false; } }
function reactivateForTimelineEdit() { state.pose = 'animation'; if (state.action) { state.action.enabled = true; state.action.play(); state.action.paused = true; } }
function applyMode() {
  clearObject(state.subject); state.subject = null;
  if (state.mode === 'assembled') {
    if (!state.assembled) { updateMeta(); return; }
    state.subject = state.assembled.scene; state.mixer = state.assembledMixer;
  } else if (state.mode === 'bones') {
    const group = new THREE.Group(); group.add(state.gltf.scene); group.add(skeletonHelper(state.gltf.scene)); group.add(makeContacts()); state.subject = group; state.mixer = state.skeletonMixer;
  } else { state.subject = state.gltf.scene; state.mixer = state.skeletonMixer; }
  root.add(state.subject); updateMeta(); fit();
}
function animationSource() { return state.mode === 'assembled' && state.assembled ? state.assembled : state.gltf; }
function actionFor(name) { const source = animationSource(); return source.animations.find((clip) => clip.name === name) || source.animations[0]; }
function selectClip(name, reset = true) {
  state.clip = name; state.pose = 'animation'; const duration = Number(clipMetadata().duration_s) || 0;
  state.currentTime = selectedClipTime(state.currentTime, duration, reset); if (!state.mixer) return;
  if (state.action) state.action.stop(); const clip = actionFor(name); if (clip) { state.action = state.mixer.clipAction(clip); state.action.setLoop(clipMetadata().loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity); state.action.clampWhenFinished = true; if (reset) state.action.reset(); state.action.play(); state.action.paused = !state.playing; }
  if (state.mode === 'bones') applyMode(); else updateMeta(); setClipTime(state.currentTime);
}
function poseNeutral() {
  if (state.mode === 'assembled') { state.mode = 'mannequin'; $('mode').value = state.mode; applyMode(); }
  state.playing = false; state.pose = 'neutral'; state.currentTime = 0; state.action?.stop(); state.mixer?.stopAllAction(); state.skeletonMixer?.stopAllAction(); if (state.subject) state.subject.position.z = 0; hideContacts();
  const rotations = state.skeleton.neutral_pose?.rotations || state.motion.clips.find((c) => c.name === 'idle')?.samples?.[0]?.rotations_xyzw;
  const byName = Array.isArray(rotations) ? Object.fromEntries(rotations.map((r) => [r.bone_name, r.rotation_xyzw])) : rotations || {};
  const catalogBones = new Map(state.skeleton.bones.map((bone) => [bone.name, bone]));
  state.gltf.scene.traverse((node) => { const catalogBone = catalogBones.get(node.name); if (!node.isBone || !catalogBone || !byName[node.name]) return; node.quaternion.copy(neutralLocalQuaternion(catalogBone, byName[node.name], state.bindWorld.get(node.name), state.bindLocal.get(node.name))); });
  const rootBone = state.gltf.scene.getObjectByName('root'); const rootOffset = state.skeleton.neutral_pose?.root_offset_m;
  if (rootBone && Array.isArray(rootOffset)) { const parentWorld = new THREE.Quaternion(); rootBone.parent.getWorldQuaternion(parentWorld); rootBone.position.copy(state.bindPosition.get('root')).add(rootLocalOffset(rootOffset, parentWorld)); }
  state.gltf.scene.updateMatrixWorld(true); updateMeta(); updateTimeline(); fit();
}
function poseBind() { if (state.mode === 'assembled') { state.mode = 'mannequin'; $('mode').value = state.mode; applyMode(); } state.playing = false; state.pose = 'bind'; state.currentTime = 0; state.action?.stop(); state.mixer?.stopAllAction(); state.skeletonMixer?.stopAllAction(); if (state.subject) state.subject.position.z = 0; hideContacts(); state.gltf.scene.traverse((node) => { if (node.isSkinnedMesh) node.skeleton.pose(); }); state.gltf.scene.updateMatrixWorld(true); updateMeta(); updateTimeline(); fit(); }
function fit() {
  const object = state.subject || state.gltf?.scene; if (!object) return;
  const box = state.bounds?.clone() || new THREE.Box3().setFromObject(object); if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3()); const radius = Math.max(size.length() * .62, .6); state.radius = radius * 2.2; root.position.copy(center).multiplyScalar(-1); grid.position.y = -center.y; setCamera();
}
function setCamera() { const r = state.radius; const views = { front:[0,.1,1], side:[1,.1,0], top:[0,1,.001], threeQuarter:[.85,.36,1] }; const p = new THREE.Vector3(...views[$('camera').value]); p.normalize().multiplyScalar(r); camera.position.copy(p); camera.lookAt(0, 0, 0); }
function animatedBounds() {
  const union = new THREE.Box3(), fps = 30;
  const addBounds = (source, mixer, includeTravel) => {
    const record = () => { source.scene.updateMatrixWorld(true); source.scene.traverse((node) => { if (node.isSkinnedMesh) node.computeBoundingBox(); }); return new THREE.Box3().setFromObject(source.scene, true); };
    if (!source.animations.length) { union.union(record()); return; }
    for (const clip of source.animations) { const action = mixer.clipAction(clip).reset().play(); const samples = state.motion.clips.find((item) => item.name === clip.name)?.samples || []; for (let t = 0; t <= clip.duration; t += 1 / fps) { mixer.setTime(t); const box = record(); union.union(box); if (includeTravel) { const forward = samples[Math.min(Math.round(t * fps), samples.length - 1)]?.simulated_forward_m || 0; union.union(box.clone().translate(new THREE.Vector3(0, 0, forward))); } } action.stop(); }
    mixer.stopAllAction();
  };
  addBounds(state.gltf, state.skeletonMixer, true);
  if (state.assembled && state.assembledMixer) addBounds(state.assembled, state.assembledMixer, true);
  state.bounds = union.isEmpty() ? new THREE.Box3().setFromObject(state.gltf.scene) : union;
}
async function loadSkeleton() {
  loading.style.display = 'grid'; clearObject(state.subject); state.subject = null; state.skeleton = selectedSkeleton();
  const url = new URL(location.href); url.searchParams.set('skeleton', state.skeleton.skeleton_id); history.replaceState(null, '', url);
  state.motion = await fetch(rel(state.skeleton.asset.motion)).then((r) => { if (!r.ok) throw Error(`motion.json ${r.status}`); return r.json(); });
  state.gltf = await loader.loadAsync(rel(state.skeleton.asset.glb)); state.skeletonMixer = new THREE.AnimationMixer(state.gltf.scene); state.mixer = state.skeletonMixer; state.bindWorld.clear(); state.bindLocal.clear(); state.bindPosition.clear(); state.gltf.scene.updateMatrixWorld(true); state.gltf.scene.traverse((node) => { if (node.isBone) { const q = new THREE.Quaternion(); node.getWorldQuaternion(q); state.bindWorld.set(node.name, q); state.bindLocal.set(node.name, node.quaternion.clone()); state.bindPosition.set(node.name, node.position.clone()); } }); state.assembled = null; state.assembledMixer = null;
  if (state.skeleton.asset.assembled_glb) try { state.assembled = await loader.loadAsync(rel(state.skeleton.asset.assembled_glb)); state.assembledMixer = new THREE.AnimationMixer(state.assembled.scene); } catch (error) { console.warn('Assembled GLB unavailable', error); }
  $('clip').replaceChildren(...state.motion.clips.map((c) => new Option(c.name, c.name))); state.clip = state.motion.clips.find((c) => c.name === 'walk')?.name || state.motion.clips[0]?.name;
  $('clip').value = state.clip; animatedBounds(); selectClip(state.clip); applyMode(); loading.style.display = 'none';
}
function resize() { const { clientWidth:w, clientHeight:h } = canvas; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
let down = null; canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); }); canvas.addEventListener('pointermove', (e) => { if (!down) return; state.yaw += (e.clientX - down[0]) * .01; state.pitch = THREE.MathUtils.clamp(state.pitch + (e.clientY - down[1]) * .01, -1.35, 1.35); down = [e.clientX, e.clientY]; camera.position.setFromSphericalCoords(state.radius, Math.PI / 2 - state.pitch, state.yaw); camera.lookAt(0,0,0); }); canvas.addEventListener('pointerup', () => down = null); canvas.addEventListener('wheel', (e) => { state.radius = THREE.MathUtils.clamp(state.radius * (1 + e.deltaY * .001), .3, 100); setCamera(); });
$('skeleton').addEventListener('change', loadSkeleton); $('mode').addEventListener('change', (e) => { state.mode = e.target.value; applyMode(); selectClip(state.clip, false); }); $('clip').addEventListener('change', (e) => selectClip(e.target.value)); $('camera').addEventListener('change', setCamera); $('travel').addEventListener('change', (e) => { state.travel = e.target.checked; setClipTime(state.currentTime); });
bindTimelineControls({ scrub: $('scrub'), previous: $('previous'), next: $('next'), restart: $('restart'), rate: $('rate'), play: $('play'), pause: $('pause') }, createTimelineActions({ state, metadata: clipMetadata, setTime: setClipTime, reactivate: reactivateForTimelineEdit }));
$('neutral').onclick = poseNeutral; $('bind').onclick = poseBind;
async function start() { try { state.manifest = await fetch('./review-manifest.json').then((r) => { if (!r.ok) throw Error(`${r.status}; run a local HTTP server from this bundle`); return r.json(); }); $('library').textContent = `${state.manifest.library_id} v${state.manifest.library_version}`; for (const s of state.manifest.skeletons) $('skeleton').add(new Option(`${s.skeleton_id} · ${s.family} · ${s.status}`, s.skeleton_id)); const wanted = new URLSearchParams(location.search).get('skeleton'); if (wanted && state.manifest.skeletons.some((s) => s.skeleton_id === wanted)) $('skeleton').value = wanted; await loadSkeleton(); } catch (error) { loading.textContent = `Unable to load review bundle: ${error.message}`; console.error(error); } }
function frame(now) { requestAnimationFrame(frame); resize(); const dt = Math.min((now - (frame.last || now)) / 1000, .1); frame.last = now; if (state.playing && state.mixer) { if (state.action) state.action.paused = false; const meta = clipMetadata(), next = advanceTime(state.currentTime, dt * state.playbackRate, Number(meta.duration_s) || 0, Boolean(meta.loop)); setClipTime(next.time); if (next.ended) { state.playing = false; if (state.action) state.action.paused = true; } } renderer.render(scene, camera); }
start(); requestAnimationFrame(frame);
