import * as THREE from 'three';
import { GraphNode, GraphRelationship } from '../services/query';
import { GraphLayout, LayoutNode, LayoutEdge } from './layouts/layout';
import { TreeLayout } from './layouts/tree-layout';

// ----- Types -----

interface ClassGroup {
  id: string;
  name: string;
  kind: string;
  sourceCode: string;
  methods: MethodEntry[];
  group: THREE.Group;
  width: number;
  height: number;
  x: number;
  y: number;
  methodToggles: { pub: boolean; priv: boolean; ctor: boolean; args: boolean };
  focusedMethodId: string | null;
}

interface MethodEntry {
  id: string;
  name: string;
  returnType: string;
  parameters: string;
  mesh: THREE.Mesh;
  border: THREE.Line | null;
  classId: string;
  yOffset: number;
  visible: boolean;
  nodeVisibility: string;
  isConstructor: boolean;
  startLine: number;
  endLine: number;
}

interface EdgeEntry {
  line: THREE.Line;
  sourceId: string;
  targetId: string;
  type: string;
}

type NodeRightClickHandler = (nodeId: string, labels: string[], screenX: number, screenY: number) => void;

// ----- Constants -----

const HEADER_HEIGHT = 28;
const METHOD_ROW_HEIGHT = 24;
const METHOD_PADDING = 4;
const CLASS_PADDING = 12;
const MIN_CLASS_WIDTH = 180;
const CORNER_RADIUS = 6;
const TEX_SCALE = 2;

// Open Colors palette
const COLOR_CLASS_BG = 0xe7f5ff;       // oc-blue-0
const COLOR_CLASS_HEADER = 0x1c7ed6;   // oc-blue-7
const COLOR_METHOD_BG = 0xffffff;
const COLOR_METHOD_HOVER = 0xd0ebff;   // oc-blue-1
const COLOR_METHOD_SELECTED = 0xe5dbff; // oc-violet-1
const COLOR_METHOD_BORDER = 0xdee2e6;  // oc-gray-3
const COLOR_EDGE_IMPORTS = 0xadb5bd;   // oc-gray-5
const COLOR_EDGE_CALLS = 0x4c6ef5;     // oc-indigo-5
const COLOR_PACKAGE_BG = 0x845ef7;     // oc-violet-5

const FONT_HEADER = '700 13px Nunito, sans-serif';
const FONT_HEADER_BADGE = '600 9px Nunito, sans-serif';
const FONT_METHOD = '12px Nunito, sans-serif';
const FONT_STANDALONE = '600 12px Nunito, sans-serif';

// Arch node colors by label
const ARCH_COLORS: Record<string, { bg: number; hex: string }> = {
  RESTInterface:  { bg: 0x339af0, hex: '#339af0' },
  RESTEndpoint:   { bg: 0x74c0fc, hex: '#74c0fc' },
  FeignClient:    { bg: 0x845ef7, hex: '#845ef7' },
  FeignEndpoint:  { bg: 0xb197fc, hex: '#b197fc' },
  JMSDestination: { bg: 0x20c997, hex: '#20c997' },
  JMSListener:    { bg: 0x51cf66, hex: '#51cf66' },
  JMSProducer:    { bg: 0xff922b, hex: '#ff922b' },
  ScheduledTask:  { bg: 0xfcc419, hex: '#fcc419' },
  HTTPClient:     { bg: 0x5c7cfa, hex: '#5c7cfa' },
  Repository:     { bg: 0x22b8cf, hex: '#22b8cf' },
  Microservice:   { bg: 0x1a3a5c, hex: '#1a3a5c' },
};

function getArchColor(labels: string[]): { bg: number; hex: string } {
  for (const label of labels) {
    if (ARCH_COLORS[label]) return ARCH_COLORS[label];
  }
  return { bg: 0x6366f1, hex: '#6366f1' };
}

export type PerspectiveMode = 'java' | 'arch';


// ----- Helpers -----

function roundedRectShape(w: number, h: number, r: number): THREE.Shape {
  const shape = new THREE.Shape();
  shape.moveTo(-w / 2 + r, -h / 2);
  shape.lineTo(w / 2 - r, -h / 2);
  shape.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
  shape.lineTo(w / 2, h / 2 - r);
  shape.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
  shape.lineTo(-w / 2 + r, h / 2);
  shape.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
  shape.lineTo(-w / 2, -h / 2 + r);
  shape.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
  return shape;
}

let measureCanvas: HTMLCanvasElement | null = null;
function measureText(text: string, font: string): number {
  if (!measureCanvas) measureCanvas = document.createElement('canvas');
  const ctx = measureCanvas.getContext('2d')!;
  ctx.font = font;
  return ctx.measureText(text).width;
}

function methodLabel(m: MethodEntry, className: string, showParams = true): string {
  const params = showParams ? m.parameters : '';
  return m.isConstructor ? `${className}(${params})` : `${m.name}(${params}): ${m.returnType}`;
}

function visibilityColor(m: MethodEntry): string {
  if (m.isConstructor) return '#339af0';  // oc-blue-5
  switch (m.nodeVisibility) {
    case 'public': return '#51cf66';      // oc-green-5
    case 'private': return '#ff6b6b';     // oc-red-4
    case 'protected': return '#fcc419';   // oc-yellow-5
    default: return '#adb5bd';            // oc-gray-5
  }
}

function rectEdgePoint(cx: number, cy: number, w: number, h: number, dx: number, dy: number): { x: number; y: number } {
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const hw = w / 2;
  const hh = h / 2;
  const scaleX = Math.abs(dx) > 0 ? hw / Math.abs(dx) : Infinity;
  const scaleY = Math.abs(dy) > 0 ? hh / Math.abs(dy) : Infinity;
  const scale = Math.min(scaleX, scaleY);
  return { x: cx + dx * scale, y: cy + dy * scale };
}

function makeTexture(
  w: number, h: number,
  draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = w * TEX_SCALE;
  canvas.height = h * TEX_SCALE;
  const ctx = canvas.getContext('2d')!;
  ctx.scale(TEX_SCALE, TEX_SCALE);
  draw(ctx, w, h);
  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

// ----- GraphRenderer -----

export class GraphRenderer {
  private scene: THREE.Scene;
  private camera: THREE.OrthographicCamera;
  private renderer: THREE.WebGLRenderer;
  private raycaster = new THREE.Raycaster();
  private mouse = new THREE.Vector2();
  private container: HTMLElement;

  private classGroups = new Map<string, ClassGroup>();
  private methodEntries = new Map<string, MethodEntry>();
  private standaloneNodes = new Map<string, THREE.Group>();
  private edges: EdgeEntry[] = [];
  private allMeshes: THREE.Mesh[] = [];

  private currentLayout: GraphLayout = new TreeLayout();
  private globalToggles = { pub: true, priv: true, ctor: true, args: true };
  private perspective: PerspectiveMode = 'java';

  private hoveredMesh: THREE.Mesh | null = null;
  private isDragging = false;
  private lastMouse = { x: 0, y: 0 };
  private mouseDownPos = { x: 0, y: 0 };
  private animFrameId = 0;
  private selectedClassId: string | null = null;
  private selectedMethodId: string | null = null;
  private selectionBorder: THREE.LineLoop | null = null;

  onNodeRightClick: NodeRightClickHandler | null = null;
  onClassClick: ((classId: string) => void) | null = null;
  onMethodClick: ((classId: string, methodId: string) => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    const w = container.clientWidth || 800;
    const h = container.clientHeight || 600;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf8f9fa);  // oc-gray-0

    const aspect = w / h;
    const viewSize = 500;
    this.camera = new THREE.OrthographicCamera(
      -viewSize * aspect, viewSize * aspect,
      viewSize, -viewSize, 0.1, 1000);
    this.camera.position.set(0, 0, 100);
    this.camera.lookAt(0, 0, 0);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.setSize(w, h);
    container.appendChild(this.renderer.domElement);

    this.renderer.domElement.addEventListener('mousedown', this.onMouseDown);
    this.renderer.domElement.addEventListener('mousemove', this.onMouseMove);
    this.renderer.domElement.addEventListener('mouseup', this.onMouseUp);
    this.renderer.domElement.addEventListener('wheel', this.onWheel, { passive: false });
    this.renderer.domElement.addEventListener('contextmenu', this.onContextMenu);
    this.renderer.domElement.addEventListener('click', this.onClick);
    window.addEventListener('resize', this.onResize);

    this.animate();
  }

  // ----- Public API -----

  setPerspective(mode: PerspectiveMode) {
    if (this.perspective === mode) return;
    this.perspective = mode;
    this.clear();
  }

  getPerspective(): PerspectiveMode {
    return this.perspective;
  }

  setData(nodes: GraphNode[], relationships: GraphRelationship[]) {
    this.clear();
    this.addData(nodes, relationships);
  }

  addData(nodes: GraphNode[], relationships: GraphRelationship[]) {
    if (this.perspective === 'arch') {
      this.addDataArch(nodes, relationships);
      return;
    }

    const methodParentMap = new Map<string, string>();
    for (const rel of relationships) {
      if (rel.type === 'HAS_METHOD') {
        methodParentMap.set(rel.end_node_id, rel.start_node_id);
      }
    }

    const classNodes: GraphNode[] = [];
    const methodNodes: GraphNode[] = [];

    for (const node of nodes) {
      if (this.classGroups.has(node.id) || this.methodEntries.has(node.id) || this.standaloneNodes.has(node.id)) continue;
      if (node.labels.includes('Class')) classNodes.push(node);
      else if (node.labels.includes('Method')) methodNodes.push(node);
    }

    for (const node of classNodes) this.createClassGroup(node);
    for (const node of methodNodes) {
      const parentId = methodParentMap.get(node.id);
      if (parentId && this.classGroups.has(parentId)) this.addMethodToClass(node, parentId);
    }
    for (const cg of this.classGroups.values()) this.recalcClassSize(cg);

    for (const rel of relationships) {
      if (rel.type === 'HAS_METHOD') continue;
      if (this.edges.some(e => e.line.userData['relId'] === rel.id)) continue;
      const srcKnown = this.classGroups.has(rel.start_node_id) || this.methodEntries.has(rel.start_node_id);
      const tgtKnown = this.classGroups.has(rel.end_node_id) || this.methodEntries.has(rel.end_node_id);
      if (!srcKnown || !tgtKnown) continue;
      this.createEdge(rel);
    }

    this.runLayout();
  }

  private addDataArch(nodes: GraphNode[], relationships: GraphRelationship[]) {
    for (const node of nodes) {
      if (this.standaloneNodes.has(node.id)) continue;
      if (!node.labels.includes('Arch')) continue;
      this.createArchNode(node);
    }

    for (const rel of relationships) {
      if (this.edges.some(e => e.line.userData['relId'] === rel.id)) continue;
      const srcKnown = this.standaloneNodes.has(rel.start_node_id);
      const tgtKnown = this.standaloneNodes.has(rel.end_node_id);
      if (!srcKnown || !tgtKnown) continue;
      this.createEdge(rel);
    }

    this.runLayout();
  }

  clear() {
    // Dispose textures
    this.scene.traverse((obj: THREE.Object3D) => {
      if (obj instanceof THREE.Mesh) {
        const mat = obj.material as THREE.MeshBasicMaterial;
        if (mat.map) { mat.map.dispose(); mat.map = null; }
        mat.dispose();
        obj.geometry?.dispose();
      }
    });
    while (this.scene.children.length > 0) {
      this.scene.remove(this.scene.children[0]);
    }
    this.classGroups.clear();
    this.methodEntries.clear();
    this.standaloneNodes.clear();
    this.edges = [];
    this.allMeshes = [];
  }

  destroy() {
    cancelAnimationFrame(this.animFrameId);
    this.clear();
    this.renderer.domElement.removeEventListener('mousedown', this.onMouseDown);
    this.renderer.domElement.removeEventListener('mousemove', this.onMouseMove);
    this.renderer.domElement.removeEventListener('mouseup', this.onMouseUp);
    this.renderer.domElement.removeEventListener('wheel', this.onWheel);
    this.renderer.domElement.removeEventListener('contextmenu', this.onContextMenu);
    this.renderer.domElement.removeEventListener('click', this.onClick);
    window.removeEventListener('resize', this.onResize);
    this.renderer.dispose();
    if (this.container.contains(this.renderer.domElement))
      this.container.removeChild(this.renderer.domElement);
  }

  resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (w <= 0 || h <= 0) return;
    this.renderer.setSize(w, h);

    // Preserve current center and vertical span, adjust for new aspect ratio
    const cx = (this.camera.left + this.camera.right) / 2;
    const cy = (this.camera.top + this.camera.bottom) / 2;
    const viewH = (this.camera.top - this.camera.bottom) / 2;
    const aspect = w / h;
    const viewW = viewH * aspect;

    this.camera.left = cx - viewW;
    this.camera.right = cx + viewW;
    this.camera.top = cy + viewH;
    this.camera.bottom = cy - viewH;
    this.camera.updateProjectionMatrix();
  }

  getNodeCount(): number {
    return this.classGroups.size + this.methodEntries.size + this.standaloneNodes.size;
  }

  getEdgeCount(): number {
    return this.edges.length;
  }

  getFirstClassId(): string | null {
    const first = this.classGroups.keys().next();
    return first.done ? null : first.value;
  }

  setLayout(layout: GraphLayout) {
    this.currentLayout = layout;
    if (this.classGroups.size > 0 || this.standaloneNodes.size > 0) {
      this.runLayout();
    }
  }

  getLayoutName(): string {
    return this.currentLayout.name;
  }

  setGlobalToggles(toggles: { pub: boolean; priv: boolean; ctor: boolean; args: boolean }) {
    this.globalToggles = { ...toggles };
    for (const cg of this.classGroups.values()) {
      cg.methodToggles = { ...toggles };
      this.recalcClassSize(cg);
    }
    this.updateEdgeVisibility();
    this.runLayout();
  }

  getSourceInfo(classId: string): { className: string; sourceCode: string; methods: { id: string; name: string; startLine: number; endLine: number }[] } | null {
    const cg = this.classGroups.get(classId);
    if (!cg) return null;
    return {
      className: cg.name,
      sourceCode: cg.sourceCode,
      methods: cg.methods.map(m => ({
        id: m.id,
        name: m.isConstructor ? `${cg.name}(${m.parameters})` : `${m.name}(${m.parameters})`,
        startLine: m.startLine,
        endLine: m.endLine,
      })),
    };
  }

  setSelection(classId: string | null, methodId: string | null) {
    // Reset previous method selection
    if (this.selectedMethodId) {
      const prev = this.methodEntries.get(this.selectedMethodId);
      if (prev) (prev.mesh.material as THREE.MeshBasicMaterial).color.setHex(COLOR_METHOD_BG);
    }
    // Remove previous class selection border
    if (this.selectionBorder) {
      this.selectionBorder.parent?.remove(this.selectionBorder);
      this.selectionBorder.geometry.dispose();
      (this.selectionBorder.material as THREE.LineBasicMaterial).dispose();
      this.selectionBorder = null;
    }
    this.selectedClassId = classId;
    this.selectedMethodId = methodId;
    // Highlight selected method
    if (methodId) {
      const method = this.methodEntries.get(methodId);
      if (method) (method.mesh.material as THREE.MeshBasicMaterial).color.setHex(COLOR_METHOD_SELECTED);
    }
    // Add selection border to class
    if (classId) {
      const cg = this.classGroups.get(classId);
      if (cg) {
        const borderShape = roundedRectShape(cg.width + 4, cg.height + 4, CORNER_RADIUS + 2);
        const borderPoints = borderShape.getPoints(32);
        const borderGeo = new THREE.BufferGeometry().setFromPoints(
          borderPoints.map((p: THREE.Vector2) => new THREE.Vector3(p.x, p.y - (cg.height - HEADER_HEIGHT) / 2, 2)));
        this.selectionBorder = new THREE.LineLoop(borderGeo,
          new THREE.LineBasicMaterial({ color: 0x7950f2, linewidth: 2 }));  // oc-violet-6
        cg.group.add(this.selectionBorder);
      }
    }
  }

  focusMethod(classId: string, methodId: string) {
    const cg = this.classGroups.get(classId);
    if (!cg) return;
    cg.focusedMethodId = methodId;
    this.recalcClassSize(cg);
    this.updateEdgeVisibility();
    this.runLayout();
  }

  restoreMethods(classId: string) {
    const cg = this.classGroups.get(classId);
    if (!cg) return;
    cg.focusedMethodId = null;
    this.recalcClassSize(cg);
    this.updateEdgeVisibility();
    this.runLayout();
  }

  hasMethodFocus(classId: string): boolean {
    const cg = this.classGroups.get(classId);
    return cg ? cg.focusedMethodId !== null : false;
  }

  getMethodClassId(methodId: string): string | null {
    const method = this.methodEntries.get(methodId);
    return method ? method.classId : null;
  }

  deleteNode(nodeId: string) {
    // Delete a class
    const cg = this.classGroups.get(nodeId);
    if (cg) {
      const methodIds = new Set(cg.methods.map(m => m.id));
      for (const m of cg.methods) this.methodEntries.delete(m.id);
      this.scene.remove(cg.group);
      cg.group.traverse(obj => {
        if (obj instanceof THREE.Mesh) {
          const mat = obj.material as THREE.MeshBasicMaterial;
          if (mat.map) mat.map.dispose();
          mat.dispose();
          obj.geometry?.dispose();
        }
      });
      this.classGroups.delete(nodeId);
      this.removeEdgesFor(id => id === nodeId || methodIds.has(id));
      if (this.selectedClassId === nodeId) this.clearSelection();
      this.rebuildAllMeshes();
      this.runLayout();
      return;
    }

    // Delete a method
    const method = this.methodEntries.get(nodeId);
    if (method) {
      const parentCg = this.classGroups.get(method.classId);
      if (parentCg) {
        parentCg.group.remove(method.mesh);
        if (method.border) parentCg.group.remove(method.border);
        const mat = method.mesh.material as THREE.MeshBasicMaterial;
        if (mat.map) mat.map.dispose();
        mat.dispose();
        method.mesh.geometry?.dispose();
        if (method.border) {
          method.border.geometry.dispose();
          (method.border.material as THREE.Material).dispose();
        }
        parentCg.methods = parentCg.methods.filter(m => m.id !== nodeId);
        this.methodEntries.delete(nodeId);
        this.removeEdgesFor(id => id === nodeId);
        if (this.selectedMethodId === nodeId) this.selectedMethodId = null;
        this.recalcClassSize(parentCg);
      }
      this.rebuildAllMeshes();
      this.runLayout();
      return;
    }

    // Delete standalone
    const standalone = this.standaloneNodes.get(nodeId);
    if (standalone) {
      this.scene.remove(standalone);
      standalone.traverse(obj => {
        if (obj instanceof THREE.Mesh) {
          const mat = obj.material as THREE.MeshBasicMaterial;
          if (mat.map) mat.map.dispose();
          mat.dispose();
          obj.geometry?.dispose();
        }
      });
      this.standaloneNodes.delete(nodeId);
      this.removeEdgesFor(id => id === nodeId);
      this.rebuildAllMeshes();
      this.runLayout();
    }
  }

  private removeEdgesFor(match: (id: string) => boolean) {
    this.edges = this.edges.filter(e => {
      if (match(e.sourceId) || match(e.targetId)) {
        this.scene.remove(e.line);
        e.line.geometry.dispose();
        (e.line.material as THREE.Material).dispose();
        const arrow = e.line.userData['arrow'] as THREE.Mesh | undefined;
        if (arrow) { arrow.geometry.dispose(); (arrow.material as THREE.Material).dispose(); }
        return false;
      }
      return true;
    });
  }

  private clearSelection() {
    if (this.selectionBorder) {
      this.selectionBorder.parent?.remove(this.selectionBorder);
      this.selectionBorder.geometry.dispose();
      (this.selectionBorder.material as THREE.LineBasicMaterial).dispose();
      this.selectionBorder = null;
    }
    this.selectedClassId = null;
    this.selectedMethodId = null;
  }

  focusOnClass(classId: string) {
    for (const [id, cg] of this.classGroups) {
      if (id === classId) continue;
      this.scene.remove(cg.group);
      for (const m of cg.methods) this.methodEntries.delete(m.id);
    }
    const focused = this.classGroups.get(classId);
    this.classGroups.clear();
    if (focused) this.classGroups.set(classId, focused);

    for (const [, group] of this.standaloneNodes) this.scene.remove(group);
    this.standaloneNodes.clear();

    for (const e of this.edges) this.scene.remove(e.line);
    this.edges = [];

    this.rebuildAllMeshes();
    this.fitCamera();
  }

  // ----- Create nodes -----

  private createClassGroup(node: GraphNode) {
    const name = (node.properties['name'] as string) || 'Unknown';
    const kind = (node.properties['kind'] as string) || 'class';
    const sourceCode = (node.properties['source_code'] as string) || '';

    const group = new THREE.Group();
    group.userData['nodeId'] = node.id;
    group.userData['labels'] = node.labels;
    this.scene.add(group);

    const cg: ClassGroup = {
      id: node.id, name, kind, sourceCode, methods: [],
      group, width: MIN_CLASS_WIDTH, height: HEADER_HEIGHT + CLASS_PADDING,
      x: 0, y: 0, methodToggles: { ...this.globalToggles },
      focusedMethodId: null,
    };

    this.classGroups.set(node.id, cg);
  }

  private addMethodToClass(node: GraphNode, classId: string) {
    const cg = this.classGroups.get(classId)!;
    const name = (node.properties['name'] as string) || '?';
    const params = (node.properties['parameters'] as string) || '';
    const retType = (node.properties['return_type'] as string) || 'void';
    const visibility = (node.properties['visibility'] as string) || 'package';
    const isConstructor = name === '<init>';
    const startLine = (node.properties['start_line'] as number) ?? -1;
    const endLine = (node.properties['end_line'] as number) ?? -1;

    const geo = new THREE.PlaneGeometry(1, 1);
    const mat = new THREE.MeshBasicMaterial({ color: COLOR_METHOD_BG });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData['nodeId'] = node.id;
    mesh.userData['labels'] = node.labels;
    mesh.userData['isMethod'] = true;
    cg.group.add(mesh);
    this.allMeshes.push(mesh);

    const entry: MethodEntry = {
      id: node.id, name, returnType: retType, parameters: params,
      mesh, border: null, classId, yOffset: 0,
      visible: true, nodeVisibility: visibility, isConstructor,
      startLine, endLine,
    };
    cg.methods.push(entry);
    this.methodEntries.set(node.id, entry);
  }

  private recalcClassSize(cg: ClassGroup) {
    // Apply toggle visibility (focusedMethodId overrides toggles)
    for (const m of cg.methods) {
      if (cg.focusedMethodId) {
        m.visible = m.id === cg.focusedMethodId;
      } else if (m.isConstructor) {
        m.visible = cg.methodToggles.ctor;
      } else if (m.nodeVisibility === 'public') {
        m.visible = cg.methodToggles.pub;
      } else {
        m.visible = cg.methodToggles.priv;
      }
    }
    const visibleMethods = cg.methods.filter(m => m.visible);

    // Calculate width
    let maxLabelWidth = measureText(cg.name, FONT_HEADER) + 30;
    for (const m of visibleMethods) {
      const w = measureText(methodLabel(m, cg.name, cg.methodToggles.args), FONT_METHOD) + 20;
      maxLabelWidth = Math.max(maxLabelWidth, w);
    }

    cg.width = Math.max(MIN_CLASS_WIDTH, maxLabelWidth + CLASS_PADDING * 2 + 16);
    cg.height = HEADER_HEIGHT + CLASS_PADDING +
      visibleMethods.length * (METHOD_ROW_HEIGHT + METHOD_PADDING) +
      (visibleMethods.length > 0 ? METHOD_PADDING : 0);

    // Remove old non-method children (bg, header, borders, lines)
    const toRemove: THREE.Object3D[] = [];
    cg.group.children.forEach((child: THREE.Object3D) => {
      if (child instanceof THREE.Mesh && !child.userData['isMethod']) {
        toRemove.push(child);
      }
      if ((child instanceof THREE.Line || child instanceof THREE.LineLoop) && !child.userData['methodBorder']) {
        toRemove.push(child);
      }
    });
    for (const c of toRemove) {
      if (c === this.selectionBorder) this.selectionBorder = null;
      const idx = this.allMeshes.indexOf(c as THREE.Mesh);
      if (idx >= 0) this.allMeshes.splice(idx, 1);
      cg.group.remove(c);
      if (c instanceof THREE.Mesh) {
        const m = c.material as THREE.MeshBasicMaterial;
        if (m.map) m.map.dispose();
        m.dispose();
        c.geometry?.dispose();
      }
    }

    // Class background
    const bgShape = roundedRectShape(cg.width, cg.height, CORNER_RADIUS);
    const bgGeo = new THREE.ShapeGeometry(bgShape);
    const bgMat = new THREE.MeshBasicMaterial({ color: COLOR_CLASS_BG });
    const bgMesh = new THREE.Mesh(bgGeo, bgMat);
    bgMesh.position.set(0, -(cg.height - HEADER_HEIGHT) / 2, 0);
    bgMesh.userData['nodeId'] = cg.id;
    bgMesh.userData['labels'] = ['Class'];
    bgMesh.userData['isClassBg'] = true;
    cg.group.add(bgMesh);
    this.allMeshes.push(bgMesh);

    // Header bar
    const hdrTex = makeTexture(cg.width, HEADER_HEIGHT, (ctx, w, h) => {
      ctx.fillStyle = '#d0ebff';  // oc-blue-1
      ctx.fillRect(0, 0, w, h);
      // Class name
      ctx.fillStyle = '#1864ab';  // oc-blue-9
      ctx.font = FONT_HEADER;
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.fillText(cg.name, 10, h / 2, w - 20);
      // Kind badge
      const nameW = Math.min(measureText(cg.name, FONT_HEADER), w - 20);
      ctx.fillStyle = '#495057';  // oc-gray-7
      ctx.font = FONT_HEADER_BADGE;
      ctx.fillText(cg.kind, 10 + nameW + 6, h / 2);
    });
    const hdrGeo = new THREE.PlaneGeometry(cg.width, HEADER_HEIGHT);
    const hdrMat = new THREE.MeshBasicMaterial({ map: hdrTex });
    const hdrMesh = new THREE.Mesh(hdrGeo, hdrMat);
    hdrMesh.position.set(0, 0, 0.5);
    hdrMesh.userData['isHeader'] = true;
    hdrMesh.userData['classId'] = cg.id;
    cg.group.add(hdrMesh);
    this.allMeshes.push(hdrMesh);

    // Class border
    const borderShape = roundedRectShape(cg.width, cg.height, CORNER_RADIUS);
    const borderPoints = borderShape.getPoints(32);
    const borderGeo = new THREE.BufferGeometry().setFromPoints(
      borderPoints.map((p: THREE.Vector2) => new THREE.Vector3(p.x, p.y - (cg.height - HEADER_HEIGHT) / 2, 0.6)));
    const borderLine = new THREE.LineLoop(borderGeo,
      new THREE.LineBasicMaterial({ color: COLOR_CLASS_HEADER, linewidth: 1 }));
    cg.group.add(borderLine);

    // Position methods
    const methodWidth = cg.width - CLASS_PADDING * 2;
    const mh = METHOD_ROW_HEIGHT - 2;
    let visIdx = 0;

    for (const m of cg.methods) {
      if (m.visible) {
        const yOffset = -(HEADER_HEIGHT + METHOD_PADDING + visIdx * (METHOD_ROW_HEIGHT + METHOD_PADDING));
        m.yOffset = yOffset;
        visIdx++;

        // Dispose old texture
        const oldMat = m.mesh.material as THREE.MeshBasicMaterial;
        if (oldMat.map) { oldMat.map.dispose(); }

        // Create text texture with visibility symbol
        const labelText = methodLabel(m, cg.name, cg.methodToggles.args);
        const symColor = visibilityColor(m);
        const tex = makeTexture(methodWidth, mh, (ctx, w, h) => {
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, w, h);
          // Visibility symbol
          ctx.fillStyle = symColor;
          ctx.beginPath();
          if (m.isConstructor) {
            const cx = 12, cy = h / 2, s = 4;
            ctx.moveTo(cx, cy - s);
            ctx.lineTo(cx + s, cy);
            ctx.lineTo(cx, cy + s);
            ctx.lineTo(cx - s, cy);
          } else {
            ctx.arc(12, h / 2, 3.5, 0, Math.PI * 2);
          }
          ctx.fill();
          // Method text
          ctx.fillStyle = '#212529';  // oc-gray-9
          ctx.font = FONT_METHOD;
          ctx.textBaseline = 'middle';
          ctx.textAlign = 'left';
          ctx.fillText(labelText, 24, h / 2, w - 32);
        });

        m.mesh.geometry.dispose();
        m.mesh.geometry = new THREE.PlaneGeometry(methodWidth, mh);
        m.mesh.material = new THREE.MeshBasicMaterial({ map: tex });
        m.mesh.position.set(0, yOffset, 1);
        m.mesh.visible = true;

        // Remove old border
        if (m.border) { cg.group.remove(m.border); m.border = null; }

        // Method border
        const hw = methodWidth / 2, hh = mh / 2;
        const pts = [
          new THREE.Vector3(-hw, -hh, 0), new THREE.Vector3(hw, -hh, 0),
          new THREE.Vector3(hw, hh, 0), new THREE.Vector3(-hw, hh, 0),
          new THREE.Vector3(-hw, -hh, 0),
        ];
        const bGeo = new THREE.BufferGeometry().setFromPoints(pts);
        const border = new THREE.Line(bGeo, new THREE.LineBasicMaterial({ color: COLOR_METHOD_BORDER }));
        border.position.set(0, yOffset, 1.1);
        border.userData['methodBorder'] = m.id;
        cg.group.add(border);
        m.border = border;
      } else {
        m.mesh.visible = false;
        if (m.border) { m.border.visible = false; }
      }
    }

    // Sync allMeshes: ensure visible methods are present, remove hidden ones
    for (const m of cg.methods) {
      const idx = this.allMeshes.indexOf(m.mesh);
      if (m.visible) {
        if (idx < 0) this.allMeshes.push(m.mesh);
      } else {
        if (idx >= 0) this.allMeshes.splice(idx, 1);
      }
    }
  }

  private createStandaloneNode(node: GraphNode) {
    const name = (node.properties['name'] as string) || (node.properties['full_name'] as string) || '?';
    const isPackage = node.labels.includes('Package');
    const color = isPackage ? COLOR_PACKAGE_BG : 0x6366f1;
    const hexColor = isPackage ? '#8b5cf6' : '#6366f1';

    const w = Math.max(120, measureText(name, FONT_STANDALONE) + 24);
    const h = 32;

    const tex = makeTexture(w, h, (ctx, tw, th) => {
      ctx.fillStyle = hexColor;
      this.roundRect(ctx, 0, 0, tw, th, 6);
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.font = FONT_STANDALONE;
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      ctx.fillText(name, tw / 2, th / 2, tw - 16);
    });

    const geo = new THREE.PlaneGeometry(w, h);
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData['nodeId'] = node.id;
    mesh.userData['labels'] = node.labels;

    const group = new THREE.Group();
    group.add(mesh);

    this.scene.add(group);
    this.standaloneNodes.set(node.id, group);
    this.allMeshes.push(mesh);
  }

  private createArchNode(node: GraphNode) {
    const name = (node.properties['name'] as string)
      || (node.properties['full_name'] as string) || '?';
    // For endpoints, show http_method + path
    const httpMethod = node.properties['http_method'] as string | undefined;
    const path = node.properties['path'] as string | undefined;
    const displayName = httpMethod && path ? `${httpMethod} ${path}` : name;

    // Type badge from labels (e.g., "REST Interface", "Feign Client")
    const typeBadge = this.getArchTypeBadge(node.labels);
    const color = getArchColor(node.labels);

    const nameWidth = measureText(displayName, FONT_STANDALONE);
    const badgeWidth = typeBadge ? measureText(typeBadge, FONT_HEADER_BADGE) + 12 : 0;
    const w = Math.max(160, Math.max(nameWidth, badgeWidth) + 32);
    const h = typeBadge ? 48 : 32;

    const tex = makeTexture(w, h, (ctx, tw, th) => {
      ctx.fillStyle = color.hex;
      this.roundRect(ctx, 0, 0, tw, th, 6);
      ctx.fill();

      if (typeBadge) {
        // Badge on top
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.font = FONT_HEADER_BADGE;
        ctx.textBaseline = 'top';
        ctx.textAlign = 'center';
        ctx.fillText(typeBadge, tw / 2, 6, tw - 16);
        // Name below
        ctx.fillStyle = '#ffffff';
        ctx.font = FONT_STANDALONE;
        ctx.textBaseline = 'bottom';
        ctx.fillText(displayName, tw / 2, th - 6, tw - 16);
      } else {
        ctx.fillStyle = '#ffffff';
        ctx.font = FONT_STANDALONE;
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';
        ctx.fillText(displayName, tw / 2, th / 2, tw - 16);
      }
    });

    const geo = new THREE.PlaneGeometry(w, h);
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData['nodeId'] = node.id;
    mesh.userData['labels'] = node.labels;

    const group = new THREE.Group();
    group.add(mesh);

    this.scene.add(group);
    this.standaloneNodes.set(node.id, group);
    this.allMeshes.push(mesh);
  }

  private getArchTypeBadge(labels: string[]): string {
    if (labels.includes('RESTInterface')) return 'REST';
    if (labels.includes('RESTEndpoint')) return 'ENDPOINT';
    if (labels.includes('FeignClient')) return 'FEIGN';
    if (labels.includes('FeignEndpoint')) return 'ENDPOINT';
    if (labels.includes('JMSDestination')) return 'JMS';
    if (labels.includes('JMSListener')) return 'LISTENER';
    if (labels.includes('JMSProducer')) return 'PRODUCER';
    if (labels.includes('ScheduledTask')) return 'SCHEDULED';
    if (labels.includes('HTTPClient')) return 'HTTP';
    if (labels.includes('Repository')) return 'REPOSITORY';
    if (labels.includes('Microservice')) return 'SERVICE';
    return '';
  }

  private roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  private createEdge(rel: GraphRelationship) {
    const isCalls = rel.type === 'CALLS';
    const isArchLink = rel.type === 'HAS_ENDPOINT' || rel.type === 'LISTENS_ON' || rel.type === 'SENDS_TO' || rel.type === 'IMPLEMENTED_BY';
    const color = isCalls ? COLOR_EDGE_CALLS : isArchLink ? 0x868e96 : COLOR_EDGE_IMPORTS;

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position',
      new THREE.Float32BufferAttribute([0, 0, 3, 0, 0, 3], 3));

    let mat: THREE.LineBasicMaterial | THREE.LineDashedMaterial;
    if (isCalls) {
      mat = new THREE.LineBasicMaterial({ color });
    } else {
      mat = new THREE.LineDashedMaterial({ color, dashSize: 6, gapSize: 4 });
    }

    const line = new THREE.Line(geo, mat);
    line.userData['relId'] = rel.id;
    line.computeLineDistances();
    this.scene.add(line);

    const arrowGeo = new THREE.ConeGeometry(4, 10, 3);
    const arrowMat = new THREE.MeshBasicMaterial({ color });
    const arrow = new THREE.Mesh(arrowGeo, arrowMat);
    arrow.position.z = 3;
    line.add(arrow);
    line.userData['arrow'] = arrow;

    this.edges.push({
      line, sourceId: rel.start_node_id,
      targetId: rel.end_node_id, type: rel.type,
    });
  }

  // ----- Helpers -----

  private rebuildAllMeshes() {
    this.allMeshes = [];
    for (const cg of this.classGroups.values()) {
      cg.group.children.forEach((child: THREE.Object3D) => {
        if (child instanceof THREE.Mesh && (child.userData['isClassBg'] || child.userData['isMethod'] || child.userData['isHeader'])) {
          this.allMeshes.push(child);
        }
      });
    }
    for (const [, group] of this.standaloneNodes) {
      group.children.forEach((child: THREE.Object3D) => {
        if (child instanceof THREE.Mesh) this.allMeshes.push(child);
      });
    }
  }

  // ----- Layout -----

  private runLayout() {
    // Build layout nodes
    const layoutNodes: LayoutNode[] = [];
    for (const cg of this.classGroups.values()) {
      layoutNodes.push({ id: cg.id, width: cg.width, height: cg.height });
    }
    for (const [id, group] of this.standaloneNodes) {
      const mesh = group.children[0] as THREE.Mesh;
      const geo = mesh.geometry as THREE.PlaneGeometry;
      layoutNodes.push({ id, width: geo.parameters.width, height: geo.parameters.height });
    }

    if (layoutNodes.length === 0) return;

    // Build class-level edges (resolve method → class)
    const layoutEdges: LayoutEdge[] = [];
    const seenEdge = new Set<string>();
    for (const edge of this.edges) {
      let src = edge.sourceId;
      let tgt = edge.targetId;
      const srcM = this.methodEntries.get(src);
      const tgtM = this.methodEntries.get(tgt);
      if (srcM) src = srcM.classId;
      if (tgtM) tgt = tgtM.classId;
      const key = `${src}->${tgt}`;
      if (seenEdge.has(key)) continue;
      seenEdge.add(key);
      layoutEdges.push({ sourceId: src, targetId: tgt, type: edge.type });
    }

    // Run layout strategy
    const positions = this.currentLayout.layout(layoutNodes, layoutEdges);

    // Apply positions
    for (const [id, pos] of positions) {
      const cg = this.classGroups.get(id);
      if (cg) {
        cg.x = pos.x;
        cg.y = pos.y;
        cg.group.position.set(cg.x, cg.y, 0);
      }
      const standalone = this.standaloneNodes.get(id);
      if (standalone) {
        standalone.position.set(pos.x, pos.y, 0);
      }
    }

    this.updateEdgePositions();
    this.fitCamera();
  }

  private updateEdgePositions() {
    for (const edge of this.edges) {
      const src = this.getNodeBounds(edge.sourceId);
      const tgt = this.getNodeBounds(edge.targetId);
      if (!src || !tgt) continue;

      // Connect side-to-side: right-center of source → left-center of target
      let srcPt, tgtPt;
      if (src.x <= tgt.x) {
        srcPt = { x: src.x + src.w / 2, y: src.y };
        tgtPt = { x: tgt.x - tgt.w / 2, y: tgt.y };
      } else {
        srcPt = { x: src.x - src.w / 2, y: src.y };
        tgtPt = { x: tgt.x + tgt.w / 2, y: tgt.y };
      }

      const positions = edge.line.geometry.getAttribute('position');
      positions.setXYZ(0, srcPt.x, srcPt.y, 3);
      positions.setXYZ(1, tgtPt.x, tgtPt.y, 3);
      positions.needsUpdate = true;
      edge.line.computeLineDistances();

      const arrow = edge.line.userData['arrow'] as THREE.Mesh;
      if (arrow) {
        const dx = tgtPt.x - srcPt.x;
        const dy = tgtPt.y - srcPt.y;
        arrow.position.set(tgtPt.x, tgtPt.y, 3);
        const angle = Math.atan2(dy, dx);
        arrow.rotation.z = angle - Math.PI / 2;
      }
    }
  }

  private updateEdgeVisibility() {
    for (const edge of this.edges) {
      const srcMethod = this.methodEntries.get(edge.sourceId);
      const tgtMethod = this.methodEntries.get(edge.targetId);
      const srcHidden = srcMethod !== undefined && !srcMethod.visible;
      const tgtHidden = tgtMethod !== undefined && !tgtMethod.visible;
      edge.line.visible = !srcHidden && !tgtHidden;
    }
  }

  private getNodeBounds(id: string): { x: number; y: number; w: number; h: number } | null {
    const cg = this.classGroups.get(id);
    if (cg) return { x: cg.x, y: cg.y, w: cg.width, h: cg.height };

    const method = this.methodEntries.get(id);
    if (method) {
      const parentCg = this.classGroups.get(method.classId);
      if (parentCg) {
        return {
          x: parentCg.x,
          y: parentCg.y + method.yOffset,
          w: parentCg.width - CLASS_PADDING * 2,
          h: METHOD_ROW_HEIGHT - 2,
        };
      }
    }

    const standalone = this.standaloneNodes.get(id);
    if (standalone) return { x: standalone.position.x, y: standalone.position.y, w: 120, h: 32 };

    return null;
  }

  private fitCamera() {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    for (const cg of this.classGroups.values()) {
      minX = Math.min(minX, cg.x - cg.width / 2);
      maxX = Math.max(maxX, cg.x + cg.width / 2);
      minY = Math.min(minY, cg.y - cg.height);
      maxY = Math.max(maxY, cg.y + HEADER_HEIGHT);
    }

    for (const [, group] of this.standaloneNodes) {
      minX = Math.min(minX, group.position.x - 60);
      maxX = Math.max(maxX, group.position.x + 60);
      minY = Math.min(minY, group.position.y - 16);
      maxY = Math.max(maxY, group.position.y + 16);
    }

    if (!isFinite(minX)) return;

    const margin = 80;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = (maxX - minX) + margin * 2;
    const h = (maxY - minY) + margin * 2;
    const containerAspect = this.container.clientWidth / this.container.clientHeight;
    const graphAspect = w / h;

    let viewW: number, viewH: number;
    if (graphAspect > containerAspect) {
      viewW = w / 2;
      viewH = viewW / containerAspect;
    } else {
      viewH = h / 2;
      viewW = viewH * containerAspect;
    }

    this.camera.left = cx - viewW;
    this.camera.right = cx + viewW;
    this.camera.top = cy + viewH;
    this.camera.bottom = cy - viewH;
    this.camera.updateProjectionMatrix();
  }

  // ----- Event handlers -----

  private onMouseDown = (e: MouseEvent) => {
    if (e.button === 0) {
      this.isDragging = true;
      this.mouseDownPos = { x: e.clientX, y: e.clientY };
      this.lastMouse = { x: e.clientX, y: e.clientY };
    }
  };

  private onMouseUp = () => { this.isDragging = false; };

  private onClick = (e: MouseEvent) => {
    const dx = e.clientX - this.mouseDownPos.x;
    const dy = e.clientY - this.mouseDownPos.y;
    if (dx * dx + dy * dy > 9) return;

    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.mouse, this.camera);
    const intersects = this.raycaster.intersectObjects(this.allMeshes);

    if (intersects.length > 0) {
      const mesh = intersects[0].object as THREE.Mesh;
      if (mesh.userData['isMethod']) {
        const nodeId = mesh.userData['nodeId'] as string;
        const method = this.methodEntries.get(nodeId);
        if (method && this.onMethodClick) {
          this.onMethodClick(method.classId, nodeId);
        }
      } else if (mesh.userData['isHeader'] || mesh.userData['isClassBg']) {
        const classId = (mesh.userData['classId'] || mesh.userData['nodeId']) as string;
        if (classId && this.onClassClick) {
          this.onClassClick(classId);
        }
      }
    }
  };

  private onMouseMove = (e: MouseEvent) => {
    if (this.isDragging) {
      const dx = e.clientX - this.lastMouse.x;
      const dy = e.clientY - this.lastMouse.y;
      this.lastMouse = { x: e.clientX, y: e.clientY };

      const viewWidth = this.camera.right - this.camera.left;
      const scaleX = viewWidth / this.container.clientWidth;
      const scaleY = (this.camera.top - this.camera.bottom) / this.container.clientHeight;

      this.camera.left -= dx * scaleX;
      this.camera.right -= dx * scaleX;
      this.camera.top += dy * scaleY;
      this.camera.bottom += dy * scaleY;
      this.camera.updateProjectionMatrix();
      return;
    }

    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.mouse, this.camera);
    const intersects = this.raycaster.intersectObjects(this.allMeshes);

    // Reset hover (preserve selection color)
    if (this.hoveredMesh && this.hoveredMesh.userData['isMethod']) {
      const isSelected = this.hoveredMesh.userData['nodeId'] === this.selectedMethodId;
      (this.hoveredMesh.material as THREE.MeshBasicMaterial).color.setHex(
        isSelected ? COLOR_METHOD_SELECTED : COLOR_METHOD_BG);
    }
    this.hoveredMesh = null;
    this.renderer.domElement.style.cursor = 'default';

    if (intersects.length > 0) {
      const mesh = intersects[0].object as THREE.Mesh;
      if (mesh.userData['nodeId']) {
        if (mesh.userData['isMethod']) {
          (mesh.material as THREE.MeshBasicMaterial).color.setHex(COLOR_METHOD_HOVER);
        }
        this.hoveredMesh = mesh;
        this.renderer.domElement.style.cursor = 'pointer';
      }
    }
  };

  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.1 : 0.9;
    const viewWidth = this.camera.right - this.camera.left;
    const viewHeight = this.camera.top - this.camera.bottom;
    const cx = (this.camera.left + this.camera.right) / 2;
    const cy = (this.camera.top + this.camera.bottom) / 2;

    this.camera.left = cx - (viewWidth * factor) / 2;
    this.camera.right = cx + (viewWidth * factor) / 2;
    this.camera.top = cy + (viewHeight * factor) / 2;
    this.camera.bottom = cy - (viewHeight * factor) / 2;
    this.camera.updateProjectionMatrix();
  };

  private onContextMenu = (e: MouseEvent) => {
    e.preventDefault();
    if (!this.onNodeRightClick) return;

    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.mouse, this.camera);
    const intersects = this.raycaster.intersectObjects(this.allMeshes);

    if (intersects.length > 0) {
      const mesh = intersects[0].object as THREE.Mesh;
      let nodeId = mesh.userData['nodeId'] as string | undefined;
      let labels = (mesh.userData['labels'] as string[]) || [];
      // Header mesh has classId instead of nodeId
      if (mesh.userData['isHeader']) {
        nodeId = mesh.userData['classId'] as string;
        labels = ['Class'];
      }
      if (nodeId) this.onNodeRightClick(nodeId, labels, e.clientX, e.clientY);
    }
  };

  private onResize = () => {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.renderer.setSize(w, h);
  };

  // ----- Render loop -----

  private animate = () => {
    this.animFrameId = requestAnimationFrame(this.animate);
    this.renderer.render(this.scene, this.camera);
  };
}
