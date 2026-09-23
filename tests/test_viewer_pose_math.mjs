import assert from 'node:assert/strict';
import * as THREE from '../src/critter_crafter/skeletons/viewer/vendor/three.module.js';
import { neutralLocalQuaternion, rootLocalOffset } from '../src/critter_crafter/skeletons/viewer/pose-math.mjs';

const bone = { head_m: [0, 0, 0], tail_m: [0, 1, 0], up_m: [0, 0, 1] };
const parent = new THREE.Object3D(), child = new THREE.Object3D(); parent.add(child);
const bindParent = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), .4);
const bindChild = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), .2);
parent.quaternion.copy(bindParent); child.quaternion.copy(bindChild); parent.updateWorldMatrix(true, true);
const bindWorld = child.getWorldQuaternion(new THREE.Quaternion());
const delta = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), .3).toArray();
child.quaternion.copy(neutralLocalQuaternion(bone, delta, bindWorld, bindChild));
parent.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), .5)); parent.updateWorldMatrix(true, true);
const expectedChildWorld = parent.getWorldQuaternion(new THREE.Quaternion()).multiply(child.quaternion.clone());
assert.ok(child.getWorldQuaternion(new THREE.Quaternion()).angleTo(expectedChildWorld) < 1e-7, 'child inherits altered parent rotation');
const parentWorld = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2);
assert.ok(rootLocalOffset([0, 1, 0], parentWorld).distanceTo(new THREE.Vector3(1, 0, 0)) < 1e-7, 'root offset converts from world to parent local');
