// GPU agent simulation layer: two passes, both on the GPU.
//
// 1. Simulation pass (transform feedback). A vertex shader reads every resident's day from static
//    buffers (quantised home/stop coordinates, six schedule times, colour, category bytes) and writes
//    where they are now, and what colour they should be, into two output GPU buffers. This runs only
//    when the clock, colour mode or data changes, never because the camera moved.
// 2. Draw pass. A minimal vertex shader projects the stored position and draws one tiny instanced quad
//    per resident. Panning, zooming and picking only pay for this. (Quads rather than gl_PointSize sprites:
//    on Windows, Chrome runs WebGL on Direct3D 11 via ANGLE, which emulates point sizes with a slow
//    geometry-shader path.)
//
// Nothing is computed or uploaded per frame on the CPU; the day itself is uploaded once. The draw pass reads
// 12 bytes per resident (float32 lon/lat + packed RGBA8 colour).

import { Layer, project32, picking, type DefaultProps, type LayerProps, type UpdateParameters } from '@deck.gl/core'
import { Buffer, type Device } from '@luma.gl/core'
import { BufferTransform, Geometry, Model } from '@luma.gl/engine'
import type { ShaderModule } from '@luma.gl/shadertools'

export type ColourMode = 0 | 1 | 2 | 3 // activity | signature | travel mode | age

type SimUniformProps = { bbox: [number, number, number, number]; time: number; colourMode: number; radiusPixels: number; pixelRatio: number }

const uniformBlock = /* glsl */ `\
layout(std140) uniform simUniforms {
  vec4 bbox;
  float time;
  float colourMode;
  float radiusPixels;
  float pixelRatio;
} sim;
`

const simUniforms = {
  name: 'sim',
  vs: uniformBlock,
  fs: uniformBlock,
  uniformTypes: { bbox: 'vec4<f32>', time: 'f32', colourMode: 'f32', radiusPixels: 'f32', pixelRatio: 'f32' },
} as const satisfies ShaderModule<SimUniformProps>

// ------------------------------------------------------------------ pass 1: simulate (transform feedback)
const simulateVs = /* glsl */ `\
#version 300 es
#define SHADER_NAME sim-layer-simulate
in vec2 instHome;      // unorm16, quantised within sim.bbox
in vec2 instS1;
in vec2 instS2;
in vec4 instTimesA;    // unorm16 x 65535 = minutes: leave home, arrive s1, leave s1, arrive s2
in vec2 instTimesB;    //                            leave s2, arrive home
in vec4 instColours;   // signature colour
in vec4 instMeta;      // unorm8 x 255: travel mode, age, stop-1 activity, stop-2 activity
out vec2 outPosition;  // lon, lat
flat out uint outColour;  // RGBA8 packed into one 32-bit word, read back by the draw pass as unorm8x4

const float NONE = 65535.0;
const vec3 ACT[8] = vec3[8](vec3(74.0, 110.0, 140.0), vec3(222.0, 178.0, 60.0), vec3(95.0, 184.0, 176.0),
  vec3(95.0, 184.0, 176.0), vec3(224.0, 122.0, 155.0), vec3(224.0, 122.0, 155.0), vec3(150.0, 130.0, 230.0),
  vec3(235.0, 96.0, 80.0));
const vec3 MODE[6] = vec3[6](vec3(70.0, 84.0, 96.0), vec3(155.0, 211.0, 122.0), vec3(95.0, 184.0, 176.0),
  vec3(222.0, 178.0, 60.0), vec3(150.0, 130.0, 230.0), vec3(235.0, 110.0, 90.0));
const vec3 MOVING = vec3(245.0, 245.0, 240.0);

vec2 deq(vec2 q) { return mix(sim.bbox.xy, sim.bbox.zw, q); }

void main(void) {
  vec4 ta = floor(instTimesA * NONE + 0.5);
  vec2 tb = floor(instTimesB * NONE + 0.5);
  float t = sim.time;
  vec2 home = deq(instHome);
  vec2 s1 = deq(instS1);
  vec2 s2 = deq(instS2);
  vec2 p = home;
  float state = 0.0; // 0 home, 1 at stop 1, 2 at stop 2, 3 travelling
  if (ta.y < NONE && t >= ta.x) {
    bool hasS2 = ta.w < NONE;
    if (t < ta.y) { p = mix(home, s1, (t - ta.x) / max(1.0, ta.y - ta.x)); state = 3.0; }
    else if (t < ta.z) { p = s1; state = 1.0; }
    else if (hasS2 && t < ta.w) { p = mix(s1, s2, (t - ta.z) / max(1.0, ta.w - ta.z)); state = 3.0; }
    else if (hasS2 && t < tb.x) { p = s2; state = 2.0; }
    else if (t < tb.y) {
      vec2 from = hasS2 ? s2 : s1;
      float ts = hasS2 ? tb.x : ta.z;
      p = mix(from, home, (t - ts) / max(1.0, tb.y - ts));
      state = 3.0;
    }
  }

  vec4 m = floor(instMeta * 255.0 + 0.5);
  int mode = int(sim.colourMode + 0.5);
  vec3 c;
  if (mode == 1) {
    c = instColours.rgb * 255.0;
  } else if (mode == 2) {
    c = MODE[min(int(m.x), 5)];
  } else if (mode == 3) {
    c = m.y < 18.0 ? vec3(95.0, 184.0, 176.0) : m.y < 40.0 ? vec3(155.0, 211.0, 122.0)
      : m.y < 65.0 ? vec3(222.0, 178.0, 60.0) : vec3(235.0, 110.0, 90.0);
  } else {
    c = state == 3.0 ? MOVING : state == 0.0 ? ACT[0] : ACT[min(int(state == 1.0 ? m.z : m.w), 7)];
  }
  outPosition = p;
  uvec3 u = uvec3(clamp(floor(c + 0.5), 0.0, 255.0));  // packUnorm4x8 is ES 3.1 only, so pack by hand
  outColour = u.r | (u.g << 8) | (u.b << 16) | (255u << 24);
  gl_Position = vec4(0.0);
  gl_PointSize = 1.0;
}
`

// ------------------------------------------------------------------ pass 2: draw (every frame)
const drawVs = /* glsl */ `\
#version 300 es
#define SHADER_NAME sim-layer-draw-vertex
in vec3 positions;
in vec2 simPosition;
in vec4 simColour;
out vec4 vColor;
out vec2 vUnit;
void main(void) {
  vec3 pos = vec3(simPosition, 0.0);
  geometry.worldPosition = pos;
  geometry.pickingColor = picking_getPickingColorFromInstanceID();
  vUnit = positions.xy;
  // at least ~1 device pixel wide so far-out views never drop dots between pixel centres
  float r = max(sim.radiusPixels, 0.5 / sim.pixelRatio);
  gl_Position = project_position_to_clipspace(pos, vec3(0.0), positions * project_pixel_size(r), geometry.position);
  DECKGL_FILTER_GL_POSITION(gl_Position, geometry);
  vColor = simColour;
  DECKGL_FILTER_COLOR(vColor, geometry);
}
`

const drawFs = /* glsl */ `\
#version 300 es
#define SHADER_NAME sim-layer-draw-fragment
precision highp float;
in vec4 vColor;
in vec2 vUnit;
out vec4 fragColor;
void main(void) {
  vec2 uv = vUnit;
  geometry.uv = uv;
  if (sim.radiusPixels > 1.0 && dot(uv, uv) > 1.0) discard;  // round dots once they're big enough to see
  fragColor = vColor;
  DECKGL_FILTER_COLOR(fragColor, geometry);
}
`

const INPUT_LAYOUT = [
  { name: 'instHome', format: 'unorm16x2' },
  { name: 'instS1', format: 'unorm16x2' },
  { name: 'instS2', format: 'unorm16x2' },
  { name: 'instTimesA', format: 'unorm16x4' },
  { name: 'instTimesB', format: 'unorm16x2' },
  { name: 'instColours', format: 'unorm8x4' },
  { name: 'instMeta', format: 'unorm8x4' },
] as const

export type SimLayerProps = LayerProps & {
  /** Binary columns, keyed by attribute name (instHome, instS1, …). Keep the object's identity stable. */
  data: { length: number; attributes: Record<string, { value: ArrayBufferView; size: number }> }
  time: number
  colourMode: ColourMode
  bbox: [number, number, number, number]
  radiusMeters: number
  radiusMinPixels: number
  radiusMaxPixels: number
  /** Fraction of residents to draw (level of detail). Residents are stored in spatially random order,
   *  so drawing a prefix of each chunk gives a uniform sample. Changing it costs nothing: no re-upload, no re-simulation. */
  drawFraction: number
}

const defaultProps: DefaultProps<SimLayerProps> = {
  time: 480,
  colourMode: 0,
  bbox: { type: 'array', value: [0, 0, 1, 1], compare: true },
  radiusMeters: 6,
  radiusMinPixels: 0.7,
  radiusMaxPixels: 6,
  drawFraction: 1,
}

type SimState = {
  model?: Model
  transform?: BufferTransform
  inputs?: Record<string, Buffer>
  position?: Buffer
  colour?: Buffer
}

export class SimLayer extends Layer<SimLayerProps> {
  static layerName = 'SimLayer'
  static defaultProps = defaultProps
  declare state: SimState

  getShaders() {
    return super.getShaders({ vs: drawVs, fs: drawFs, modules: [project32, picking, simUniforms] })
  }

  initializeState() {
    this.setState({})
  }

  updateState(params: UpdateParameters<this>) {
    super.updateState(params)
    const { props, oldProps, changeFlags } = params
    const device = this.context.device
    const dataChanged = props.data !== oldProps.data || !this.state.inputs

    if (dataChanged) {
      this._releaseBuffers()
      const n = props.data.length
      const inputs: Record<string, Buffer> = {}
      for (const { name } of INPUT_LAYOUT) {
        inputs[name] = device.createBuffer({ data: props.data.attributes[name].value as Uint8Array })
      }
      const position = device.createBuffer({ byteLength: Math.max(8, n * 8), usage: Buffer.VERTEX | Buffer.COPY_SRC })
      const colour = device.createBuffer({ byteLength: Math.max(4, n * 4), usage: Buffer.VERTEX | Buffer.COPY_SRC })
      const transform = new BufferTransform(device, {
        id: `${props.id}-simulate`,
        vs: simulateVs,
        modules: [simUniforms],
        bufferLayout: INPUT_LAYOUT.map((l) => ({ ...l })),
        attributes: inputs,
        vertexCount: n,
        outputs: ['outPosition', 'outColour'],
        feedbackBuffers: { outPosition: position, outColour: colour },
      })
      this.setState({ inputs, position, colour, transform })
    }

    if (dataChanged || changeFlags.extensionsChanged || !this.state.model) {
      this.state.model?.destroy()
      this.setState({ model: this._getModel(device) })
    }

    // Re-simulate only when what people are doing (or how they're coloured) changes, not when the camera moves.
    if (dataChanged || props.time !== oldProps.time || props.colourMode !== oldProps.colourMode || props.bbox !== oldProps.bbox) {
      this._simulate()
    }
  }

  finalizeState(context: Parameters<Layer['finalizeState']>[0]) {
    super.finalizeState(context)
    this.state.model?.destroy()
    this._releaseBuffers()
  }

  _simulate() {
    const { transform } = this.state
    if (!transform) return
    const { time, colourMode, bbox } = this.props
    transform.model.shaderInputs.setProps({ sim: { bbox, time, colourMode, radiusPixels: 1, pixelRatio: 1 } })
    transform.run()
    this.setNeedsRedraw()
  }

  _releaseBuffers() {
    const { transform, inputs, position, colour } = this.state
    transform?.destroy()
    for (const b of Object.values(inputs ?? {})) b.destroy()
    position?.destroy()
    colour?.destroy()
    this.setState({ transform: undefined, inputs: undefined, position: undefined, colour: undefined })
  }

  draw() {
    const { model } = this.state
    if (!model) return
    const { time, colourMode, bbox, radiusMeters, radiusMinPixels, radiusMaxPixels } = this.props
    const vp = this.context.viewport as unknown as { zoom: number; latitude?: number }
    const metersPerPixel = (40075016.686 * Math.cos(((vp.latitude ?? 0) * Math.PI) / 180)) / (512 * 2 ** vp.zoom)
    const radiusPixels = Math.min(radiusMaxPixels, Math.max(radiusMinPixels, radiusMeters / metersPerPixel))
    const canvas = (this.context.device as unknown as { canvasContext?: { cssToDeviceRatio?: () => number } }).canvasContext
    const pixelRatio = canvas?.cssToDeviceRatio?.() ?? window.devicePixelRatio ?? 1
    model.shaderInputs.setProps({ sim: { bbox, time, colourMode, radiusPixels, pixelRatio } })
    const n = this.props.data.length
    model.setInstanceCount(Math.max(1, Math.round(n * Math.min(1, Math.max(0, this.props.drawFraction)))))
    model.draw(this.context.renderPass)
  }

  _getModel(device: Device) {
    const { position, colour } = this.state
    return new Model(device, {
      ...this.getShaders(),
      id: this.props.id,
      bufferLayout: [
        { name: 'simPosition', format: 'float32x2', stepMode: 'instance' },
        { name: 'simColour', format: 'unorm8x4', stepMode: 'instance' },
      ],
      attributes: position && colour ? { simPosition: position, simColour: colour } : {},
      geometry: new Geometry({
        topology: 'triangle-strip',
        attributes: { positions: { size: 3, value: new Float32Array([-1, -1, 0, 1, -1, 0, -1, 1, 0, 1, 1, 0]) } },
      }),
      isInstanced: true,
      instanceCount: this.props.data.length,
    })
  }
}
