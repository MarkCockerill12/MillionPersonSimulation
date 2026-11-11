// GPU agent simulation layer.
//
// Every resident's whole day lives in static GPU buffers (quantised home / stop coordinates,
// six schedule times, colour and a few category bytes). The vertex shader works out where each
// person is, and what they're doing, from a single `time` uniform. Advancing the clock therefore
// costs one uniform write per frame, with no CPU loop and no buffer upload, however many people there are.
// Each person is a single GPU point sprite (one vertex), so the schedule maths runs once per person per frame.

import { Layer, project32, picking, type DefaultProps, type LayerProps, type UpdateParameters } from '@deck.gl/core'
import { Geometry, Model } from '@luma.gl/engine'
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

const vs = /* glsl */ `\
#version 300 es
#define SHADER_NAME sim-layer-vertex-shader
in vec3 positions;
in vec2 instHome;      // unorm16, quantised within sim.bbox
in vec2 instS1;
in vec2 instS2;
in vec4 instTimesA;    // unorm16 x 65535 = minutes: leave home, arrive s1, leave s1, arrive s2
in vec2 instTimesB;    //                            leave s2, arrive home
in vec4 instColours;   // signature colour
in vec4 instMeta;      // unorm8 x 255: travel mode, age, stop-1 activity, stop-2 activity
out vec4 vColor;

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

  vec3 pos = vec3(p, 0.0);
  geometry.worldPosition = pos;
  geometry.pickingColor = picking_getPickingColorFromInstanceID();
  gl_Position = project_position_to_clipspace(pos, vec3(0.0), vec3(0.0), geometry.position);
  DECKGL_FILTER_GL_POSITION(gl_Position, geometry);
  gl_PointSize = max(1.0, 2.0 * sim.radiusPixels * sim.pixelRatio);

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
  vColor = vec4(c / 255.0, 1.0);
  DECKGL_FILTER_COLOR(vColor, geometry);
}
`

const fs = /* glsl */ `\
#version 300 es
#define SHADER_NAME sim-layer-fragment-shader
precision highp float;
in vec4 vColor;
out vec4 fragColor;
void main(void) {
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  geometry.uv = uv;
  if (sim.radiusPixels > 1.0 && dot(uv, uv) > 1.0) discard;  // round dots once they're big enough to see
  fragColor = vColor;
  DECKGL_FILTER_COLOR(fragColor, geometry);
}
`

export type SimLayerProps = LayerProps & {
  /** Binary columns, keyed by attribute name (instHome, instS1, …). */
  data: { length: number; attributes: Record<string, { value: ArrayBufferView; size: number }> }
  time: number
  colourMode: ColourMode
  bbox: [number, number, number, number]
  radiusMeters: number
  radiusMinPixels: number
  radiusMaxPixels: number
}

const defaultProps: DefaultProps<SimLayerProps> = {
  time: 480,
  colourMode: 0,
  bbox: { type: 'array', value: [0, 0, 1, 1], compare: true },
  radiusMeters: 6,
  radiusMinPixels: 0.7,
  radiusMaxPixels: 6,
}

export class SimLayer extends Layer<SimLayerProps> {
  static layerName = 'SimLayer'
  static defaultProps = defaultProps
  declare state: { model?: Model }

  getShaders() {
    return super.getShaders({ vs, fs, modules: [project32, picking, simUniforms] })
  }

  initializeState() {
    this.getAttributeManager()!.addInstanced({
      instHome: { size: 2, type: 'unorm16', noAlloc: true },
      instS1: { size: 2, type: 'unorm16', noAlloc: true },
      instS2: { size: 2, type: 'unorm16', noAlloc: true },
      instTimesA: { size: 4, type: 'unorm16', noAlloc: true },
      instTimesB: { size: 2, type: 'unorm16', noAlloc: true },
      instColours: { size: 4, type: 'unorm8', noAlloc: true },
      instMeta: { size: 4, type: 'unorm8', noAlloc: true },
    })
  }

  updateState(params: UpdateParameters<this>) {
    super.updateState(params)
    if (params.changeFlags.extensionsChanged) {
      this.state.model?.destroy()
      this.state.model = this._getModel()
      this.getAttributeManager()!.invalidateAll()
    }
  }

  draw() {
    const { time, colourMode, bbox, radiusMeters, radiusMinPixels, radiusMaxPixels } = this.props
    const vp = this.context.viewport as unknown as { zoom: number; latitude?: number }
    const metersPerPixel = (40075016.686 * Math.cos(((vp.latitude ?? 0) * Math.PI) / 180)) / (512 * 2 ** vp.zoom)
    const radiusPixels = Math.min(radiusMaxPixels, Math.max(radiusMinPixels, radiusMeters / metersPerPixel))
    const model = this.state.model!
    const canvas = (this.context.device as unknown as { canvasContext?: { cssToDeviceRatio?: () => number } }).canvasContext
    const pixelRatio = canvas?.cssToDeviceRatio?.() ?? window.devicePixelRatio ?? 1
    model.shaderInputs.setProps({ sim: { bbox, time, colourMode, radiusPixels, pixelRatio } })
    model.draw(this.context.renderPass)
  }

  _getModel() {
    return new Model(this.context.device, {
      ...this.getShaders(),
      id: this.props.id,
      bufferLayout: this.getAttributeManager()!.getBufferLayouts(),
      geometry: new Geometry({
        topology: 'point-list',
        attributes: { positions: { size: 3, value: new Float32Array([0, 0, 0]) } },
      }),
      isInstanced: true,
    })
  }
}
