import * as THREE from './vendor/three.module.js';

export function canonicalBasis(bone) {
  const head = new THREE.Vector3(...bone.head_m), tail = new THREE.Vector3(...bone.tail_m);
  const y = tail.sub(head).normalize(), up = new THREE.Vector3(...bone.up_m).normalize();
  const x = new THREE.Vector3().crossVectors(y, up).normalize(), z = new THREE.Vector3().crossVectors(x, y).normalize();
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
}

export function neutralLocalQuaternion(catalogBone, deltaXYWZ, bindWorld, bindLocal) {
  const basis = canonicalBasis(catalogBone), delta = new THREE.Quaternion().fromArray(deltaXYWZ);
  const worldDelta = basis.clone().multiply(delta).multiply(basis.clone().invert());
  const localDelta = bindWorld.clone().invert().multiply(worldDelta).multiply(bindWorld);
  return bindLocal.clone().multiply(localDelta);
}

export function rootLocalOffset(offsetM, parentWorldQuaternion) {
  return new THREE.Vector3(...offsetM).applyQuaternion(parentWorldQuaternion.clone().invert());
}
