// usage: node cpuprof.mjs <targetId> [ms]  -- samples page JS for ms, prints top self-time functions
const [id, ms = 3000] = process.argv.slice(2);
const ws = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${id}`);
let n = 0; const pend = {};
const send = (m, p = {}) => new Promise((r) => { const i = ++n; pend[i] = r; ws.send(JSON.stringify({ id: i, method: m, params: p })); });
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) pend[m.id](m.result); };
ws.onopen = async () => {
  await send('Profiler.enable'); await send('Profiler.setSamplingInterval', { interval: 200 }); await send('Profiler.start');
  await new Promise((r) => setTimeout(r, +ms));
  const { profile } = await send('Profiler.stop');
  const dt = {}; const byId = Object.fromEntries(profile.nodes.map((nd) => [nd.id, nd]));
  for (let i = 0; i < profile.samples.length; i++) {
    const nd = byId[profile.samples[i]]; const f = nd.callFrame;
    const key = `${f.functionName || '(anon)'} ${f.url.split('/').pop()}:${f.lineNumber}`;
    dt[key] = (dt[key] || 0) + (profile.timeDeltas[i] || 0);
  }
  const total = Object.values(dt).reduce((a, b) => a + b, 0);
  Object.entries(dt).sort((a, b) => b[1] - a[1]).slice(0, 15).forEach(([k, v]) => console.log(`${(100 * v / total).toFixed(1).padStart(5)}%  ${k}`));
  const focus = process.argv[4];
  if (focus) {  // print the call chains leading to a function, e.g. getError
    const parent = {}; for (const nd of profile.nodes) for (const c of nd.children || []) parent[c] = nd.id;
    const chains = {};
    for (const nd of profile.nodes) if (nd.callFrame.functionName === focus) {
      const chain = []; let p = parent[nd.id];
      while (p && chain.length < 9) { const f = byId[p].callFrame; chain.push(`${f.functionName || '(anon)'}@${f.url.split('/').pop()}:${f.lineNumber}:${f.columnNumber}`); p = parent[p]; }
      const key = chain.join(' < '); chains[key] = (chains[key] || 0) + (nd.hitCount || 0);
    }
    Object.entries(chains).sort((a, b) => b[1] - a[1]).slice(0, 4).forEach(([k, v]) => console.log('CALLERS', v, k));
  }
  process.exit(0);
};
