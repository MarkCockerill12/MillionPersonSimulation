// usage: node console.mjs <targetId>  -- prints console errors from a CDP target after reload
const id = process.argv[2];
const ws = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${id}`);
let n = 0; const send = (m, p = {}) => ws.send(JSON.stringify({ id: ++n, method: m, params: p }));
ws.onopen = () => { send('Runtime.enable'); send('Log.enable'); send('Page.reload'); setTimeout(() => process.exit(0), 15000); };
ws.onmessage = (e) => { const m = JSON.parse(e.data);
  if (m.method === 'Runtime.consoleAPICalled' && ['error','warning'].includes(m.params.type)) console.log(m.params.type, m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 300));
  if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; console.log('EXC', (d.exception?.description ?? d.text).slice(0, 900)); }
  if (m.method === 'Log.entryAdded') console.log('log', m.params.entry.level, m.params.entry.text.slice(0, 300)); };
