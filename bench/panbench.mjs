// usage: node panbench.mjs <targetId> [seconds]
// Pans the camera every animation frame through deck's onViewStateChange (the same path a mouse drag
// takes) and reports frame-time stats. Requires the page to be opened with ?debug=1.
const [id, secs = 4] = process.argv.slice(2);
const expr = `(async () => {
  const map = window.__map;
  const deck = window.__deckRef?.current?.deck;
  if (!map && !deck) return 'no map/deck (open with ?debug=1)';
  const c0 = map ? map.getCenter() : null;
  const vs0 = map ? { longitude: c0.lng, latitude: c0.lat } : (deck.viewManager.getViewState('default-view') || deck.props.viewState);
  const times = []; let last = performance.now(); const end = last + ${secs} * 1000; let i = 0;
  await new Promise((done) => {
    const tick = (now) => {
      times.push(now - last); last = now; i++;
      const viewState = { ...vs0, longitude: vs0.longitude + Math.sin(i / 30) * 0.03, latitude: vs0.latitude + Math.cos(i / 40) * 0.015 };
      if (map) map.jumpTo({ center: [viewState.longitude, viewState.latitude] });
      else if (deck.props.onViewStateChange) deck.props.onViewStateChange({ viewState, interactionState: { isDragging: true }, viewId: 'default-view' });
      else deck.setProps({ viewState });
      if (now < end) requestAnimationFrame(tick); else done();
    };
    requestAnimationFrame(tick);
  });
  times.shift(); times.sort((a, b) => a - b);
  const avg = times.reduce((a, b) => a + b, 0) / times.length;
  const pct = (p) => times[Math.min(times.length - 1, Math.floor(p * times.length))].toFixed(1);
  return { frames: times.length, avg_fps: +(1000 / avg).toFixed(1), median_ms: pct(0.5), p95_ms: pct(0.95), worst_ms: times[times.length - 1].toFixed(1), over50: times.filter((t) => t > 50).length };
})()`;
const ws = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${id}`);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id === 1) { console.log(JSON.stringify(m.result.result.value ?? m.result)); process.exit(0); } };
