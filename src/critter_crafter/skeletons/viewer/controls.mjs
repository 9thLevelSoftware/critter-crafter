export function bindTimelineControls(elements, controls) {
  elements.scrub.addEventListener('input', (event) => controls.scrub(Number(event.target.value)));
  elements.previous.onclick = controls.previous;
  elements.next.onclick = controls.next;
  elements.restart.onclick = controls.restart;
  elements.rate.addEventListener('change', (event) => controls.rate(Number(event.target.value)));
  elements.play.onclick = controls.play;
  elements.pause.onclick = controls.pause;
}

export function createTimelineActions({ state, metadata, setTime, reactivate }) {
  const edit = (time) => { state.playing = false; reactivate(); setTime(time); };
  return {
    scrub: edit,
    previous: () => edit(state.currentTime - 1 / metadata().fps),
    next: () => edit(state.currentTime + 1 / metadata().fps),
    restart: () => { reactivate(); setTime(0); },
    rate: (rate) => { state.playbackRate = rate; },
    play: () => { state.playing = true; state.pose = 'animation'; if (state.action) { state.action.paused = false; state.action.play(); } setTime(state.currentTime); },
    pause: () => { state.playing = false; if (state.action) state.action.paused = true; },
  };
}
