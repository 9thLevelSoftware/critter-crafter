import assert from 'node:assert/strict';
import { bindTimelineControls, createTimelineActions } from '../src/critter_crafter/skeletons/viewer/controls.mjs';

class Element {
  constructor() { this.listeners = {}; this.onclick = null; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  emit(name, value) { this.listeners[name]({ target: { value } }); }
  click() { this.onclick(); }
}

const elements = Object.fromEntries(['scrub', 'previous', 'next', 'restart', 'rate', 'play', 'pause'].map((name) => [name, new Element()]));
const calls = [];
bindTimelineControls(elements, {
  scrub: (value) => calls.push(['scrub', value]), previous: () => calls.push(['previous']), next: () => calls.push(['next']),
  restart: () => calls.push(['restart']), rate: (value) => calls.push(['rate', value]), play: () => calls.push(['play']), pause: () => calls.push(['pause']),
});
elements.scrub.emit('input', '0.5'); elements.previous.click(); elements.next.click(); elements.restart.click(); elements.rate.emit('change', '0.25'); elements.play.click(); elements.pause.click();
assert.deepEqual(calls, [['scrub', .5], ['previous'], ['next'], ['restart'], ['rate', .25], ['play'], ['pause']], 'DOM timeline handlers route every control to its controller action');

const state = { currentTime: .5, playing: true, pose: 'neutral', playbackRate: 1, action: { paused: true, play() { calls.push(['action.play']); } } };
const edits = [], actions = createTimelineActions({ state, metadata: () => ({ fps: 30 }), setTime: (time) => edits.push(time), reactivate: () => calls.push(['reactivate']) });
actions.scrub(.2); actions.previous(); actions.next(); actions.restart(); actions.rate(.5); actions.play(); actions.pause();
assert.deepEqual(edits, [.2, .5 - 1 / 30, .5 + 1 / 30, 0, .5], 'controller maps scrub, frame steps, restart, and play to exact clip time');
assert.equal(state.playing, false, 'pause freezes controller playback');
assert.equal(state.playbackRate, .5, 'rate control updates playback rate');
assert.equal(state.action.paused, true, 'pause freezes the active animation action');
assert.ok(calls.filter(([name]) => name === 'reactivate').length === 4, 'timeline edits reactivate an action stopped by bind or neutral');
