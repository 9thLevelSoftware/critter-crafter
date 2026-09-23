export function clampTime(time, duration) {
  return Math.max(0, Math.min(Number.isFinite(duration) ? duration : 0, Number.isFinite(time) ? time : 0));
}

export function advanceTime(time, delta, duration, loop) {
  const next = time + delta;
  if (!loop) return { time: clampTime(next, duration), ended: next >= duration };
  if (!duration) return { time: 0, ended: false };
  return { time: ((next % duration) + duration) % duration, ended: false };
}

export function frameAt(time, fps, frames) {
  return Math.max(0, Math.min(frames, Math.round(clampTime(time, frames / fps) * fps)));
}

export function selectedClipTime(previousTime, duration, reset) {
  return reset ? 0 : clampTime(previousTime, duration);
}

export function timelineLabel(pose, frame, time, duration) {
  const poseLabel = pose === 'neutral' ? 'Neutral pose' : pose === 'bind' ? 'Bind pose' : '';
  const timing = `Frame ${frame} · ${time.toFixed(2)}s / ${duration.toFixed(2)}s`;
  return poseLabel ? `${poseLabel} · ${timing}` : timing;
}
