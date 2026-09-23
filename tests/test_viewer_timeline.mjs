import assert from 'node:assert/strict';
import { advanceTime, clampTime, frameAt, selectedClipTime, timelineLabel } from '../src/critter_crafter/skeletons/viewer/timeline.mjs';

assert.equal(clampTime(-1, 2), 0, 'scrub cannot move before a clip');
assert.equal(clampTime(3, 2), 2, 'scrub cannot move past a non-loop clip');
assert.deepEqual(advanceTime(.9, .2, 1, false), { time: 1, ended: true }, 'non-loop playback stops at its final frame');
const looped = advanceTime(.9, .2, 1, true);
assert.equal(looped.ended, false, 'loop playback does not end');
assert.ok(Math.abs(looped.time - .1) < 1e-9, 'loop playback wraps to the correct time');
assert.equal(frameAt(11 / 30, 30, 12), 11, 'sample time selects the matching baked 30fps frame');
assert.equal(frameAt(2, 30, 12), 12, 'next-frame control clamps at the last baked frame');
assert.equal(selectedClipTime(.4, 1, false), .4, 'mode switches preserve the comparison timestamp');
assert.equal(selectedClipTime(.4, 1, true), 0, 'new clip selection resets the timeline');
assert.match(timelineLabel('neutral', 0, 0, 1), /^Neutral pose/, 'neutral pose is visible in the timeline state');
assert.match(timelineLabel('bind', 0, 0, 1), /^Bind pose/, 'bind pose is visible in the timeline state');
