// Raw WebGL2 throughput micro-benchmark, evaluated in the page via scripts/eval.mjs.
// Draws N instances as quads / points with a trivial shader into a 1600x1000 canvas and times gl.finish().
(async () => {
  const c = document.createElement('canvas'); c.width = 1600; c.height = 1000;
  const gl = c.getContext('webgl2', { antialias: false });
  const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(s); return s; };
  const prog = (vs, fs) => { const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); return p; };
  const fs = `#version 300 es
precision highp float; out vec4 o; void main(){ o = vec4(1.0, 0.8, 0.2, 1.0); }`;
  const vsQuad = `#version 300 es
in vec2 corner; in vec2 pos; uniform float size;
void main(){ gl_Position = vec4(pos + corner * size, 0.0, 1.0); }`;
  const vsPoint = `#version 300 es
in vec2 pos; uniform float psize;
void main(){ gl_Position = vec4(pos, 0.0, 1.0); gl_PointSize = psize; }`;
  const N = 5_000_000;
  const P = new Float32Array(N * 2);
  for (let i = 0; i < N * 2; i++) P[i] = (Math.random() * 2 - 1) * 0.5; // city-sized cluster in the middle
  const posBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, posBuf); gl.bufferData(gl.ARRAY_BUFFER, P, gl.STATIC_DRAW);
  const cornerBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const out = {};
  const time = (label, draw) => {
    const px = new Uint8Array(4);
    const sync = () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); // forces the GPU to finish
    draw(); sync();
    const t = performance.now(); for (let k = 0; k < 5; k++) draw(); sync();
    out[label] = +((performance.now() - t) / 5).toFixed(1) + ' ms';
  };
  // quads
  const pq = prog(vsQuad, fs); gl.useProgram(pq);
  const vaoQ = gl.createVertexArray(); gl.bindVertexArray(vaoQ);
  let loc = gl.getAttribLocation(pq, 'corner'); gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  loc = gl.getAttribLocation(pq, 'pos'); gl.bindBuffer(gl.ARRAY_BUFFER, posBuf); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(loc, 1);
  for (const px of [1.4, 3]) {
    gl.uniform1f(gl.getUniformLocation(pq, 'size'), px / 1000);
    for (const n of [1_000_000, 5_000_000]) time(`quads ${n / 1e6}M @${px}px`, () => gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n));
  }
  // points
  const pp = prog(vsPoint, fs); gl.useProgram(pp);
  const vaoP = gl.createVertexArray(); gl.bindVertexArray(vaoP);
  loc = gl.getAttribLocation(pp, 'pos'); gl.bindBuffer(gl.ARRAY_BUFFER, posBuf); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  for (const px of [1, 1.4, 3]) {
    gl.uniform1f(gl.getUniformLocation(pp, 'psize'), px);
    for (const n of [1_000_000, 5_000_000]) time(`points ${n / 1e6}M @${px}px`, () => gl.drawArrays(gl.POINTS, 0, n));
  }
  return out;
})()
