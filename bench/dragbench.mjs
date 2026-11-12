// usage: node dragbench.mjs <targetId> [seconds]
// Real mouse drag via CDP input events (press, many moves, release) while sampling frame times in the page.
const [id, secs = 3] = process.argv.slice(2);
const ws = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${id}`);
let n = 0; const pend = {};
const send = (m, p = {}) => new Promise((r) => { const i = ++n; pend[i] = r; ws.send(JSON.stringify({ id: i, method: m, params: p })); });
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) pend[m.id](m.result); };
const evalJs = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.value;
ws.onopen = async () => {
  const before = await evalJs('window.__map ? JSON.stringify(window.__map.getCenter()) : "no map"');
  await evalJs(`window.__frames = []; (function loop(t0){ requestAnimationFrame((t) => { window.__frames.push(t - t0); if (window.__frames.length < 100000) loop(t); }); })(performance.now()); 1`);
  let x = 800, y = 500;
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
  const end = Date.now() + secs * 1000; let i = 0;
  while (Date.now() < end) {
    i++; x = 800 + Math.sin(i / 20) * 300; y = 500 + Math.cos(i / 25) * 200;
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'left', buttons: 1 });
    await new Promise((r) => setTimeout(r, 8));
  }
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
  const stats = await evalJs(`(() => { const f = window.__frames.slice(2); window.__frames = [1e9]; f.sort((a,b)=>a-b);
    const avg = f.reduce((a,b)=>a+b,0)/f.length; const p = (q) => f[Math.min(f.length-1, Math.floor(q*f.length))].toFixed(1);
    return { frames: f.length, avg_fps: +(1000/avg).toFixed(1), median_ms: p(0.5), p95_ms: p(0.95), worst_ms: f[f.length-1].toFixed(1) }; })()`);
  const after = await evalJs('window.__map ? JSON.stringify(window.__map.getCenter()) : "no map"');
  console.log(JSON.stringify({ moves: i, moved: before !== after, ...stats }));
  process.exit(0);
};
