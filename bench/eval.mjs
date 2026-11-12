// usage: node eval.mjs <targetId> "<js expression>"  -- evaluates in the page (awaits promises) and prints JSON
const [id, expr] = process.argv.slice(2);
const ws = new WebSocket(`ws://127.0.0.1:9222/devtools/page/${id}`);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id === 1) { console.log(JSON.stringify(m.result.result.value ?? m.result.exceptionDetails?.exception?.description ?? m.result)); process.exit(0); } };
