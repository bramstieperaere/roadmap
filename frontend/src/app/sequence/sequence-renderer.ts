import * as THREE from 'three';
import { GraphNode, GraphRelationship } from '../services/query';

// ----- Types -----

export interface SequenceCall {
  callerId: string;
  callerName: string;
  callerClassId: string;
  callerClassName: string;
  targetId: string;
  targetName: string;
  targetClassId: string;
  targetClassName: string;
  depth: number;
}

interface LaneEntry {
  classId: string;
  className: string;
  x: number;
  headerMesh: THREE.Mesh;
  lifeline: THREE.Line;
  sourceCode: string;
  methods: { id: string; name: string; startLine: number; endLine: number }[];
}

interface CallArrowEntry {
  mesh: THREE.Mesh;
  labelMesh: THREE.Mesh;
  arrowHead: THREE.Mesh;
  selfLoopGroup?: THREE.Group;
  call: SequenceCall;
  y: number;
}

// ----- Constants -----

const LANE_WIDTH = 220;
const HEADER_HEIGHT = 36;
const HEADER_WIDTH = 180;
const ROW_HEIGHT = 36;
const TOP_MARGIN = 60;
const ARROW_HEAD_SIZE = 6;
const TEX_SCALE = 2;
const CORNER_RADIUS = 6;
const SELF_CALL_OFFSET = 40;

// Open Colors palette
const COLOR_HEADER_BG = 0x1c7ed6;     // oc-blue-7
const COLOR_HEADER_TEXT = '#ffffff';
const COLOR_LIFELINE = 0xdee2e6;       // oc-gray-3
const COLOR_ARROW = 0x4c6ef5;          // oc-indigo-5
const COLOR_ARROW_SELF = 0x845ef7;     // oc-violet-5
const COLOR_ARROW_HOVER = 0x339af0;    // oc-blue-5
const COLOR_LABEL_TEXT = '#212529';     // oc-gray-9
const COLOR_BG = 0xf8f9fa;            // oc-gray-0
const COLOR_SELECTION = 0x7950f2;      // oc-violet-6

const FONT_HEADER = '700 13px Nunito, sans-serif';
const FONT_LABEL = '11px Nunito, sans-serif';

// ----- Helpers -----

let measureCanvas: HTMLCanvasElement | null = null;
function measureText(text: string, font: string): number {
  if (!measureCanvas) measureCanvas = document.createElement('canvas');
  const ctx = measureCanvas.getContext('2d')!;
  ctx.font = font;
  return ctx.measureText(text).width;
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

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
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

// ----- SequenceRenderer -----

export class SequenceRenderer {
  private scene: THREE.Scene;
  private camera: THREE.OrthographicCamera;
  private renderer: THREE.WebGLRenderer;
  private raycaster = new THREE.Raycaster();
  private mouse = new THREE.Vector2();
  private container: HTMLElement;

  private lanes: LaneEntry[] = [];
  private callArrows: CallArrowEntry[] = [];
  private allMeshes: THREE.Mesh[] = [];
  private calls: SequenceCall[] = [];

  private hoveredArrow: CallArrowEntry | null = null;
  private selectedArrow: CallArrowEntry | null = null;
  private selectionBorder: THREE.LineLoop | null = null;

  private isDragging = false;
  private lastMouse = { x: 0, y: 0 };
  private mouseDownPos = { x: 0, y: 0 };
  private animFrameId = 0;

  onCallClick: ((call: SequenceCall) => void) | null = null;
  onHeaderClick: ((classId: string) => void) | null = null;
  onCallRightClick: ((call: SequenceCall, screenX: number, screenY: number) => void) | null = null;
  onHeaderRightClick: ((classId: string, screenX: number, screenY: number) => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    const w = container.clientWidth || 800;
    const h = container.clientHeight || 600;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(COLOR_BG);

    const aspect = w / h;
    const viewSize = 400;
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

  setData(
    nodes: GraphNode[],
    relationships: GraphRelationship[],
    rootMethodId: string,
  ) {
    this.clear();

    // Build lookup maps
    const nodeMap = new Map<string, GraphNode>();
    for (const n of nodes) nodeMap.set(n.id, n);

    // Map method → class
    const methodToClass = new Map<string, string>();
    for (const rel of relationships) {
      if (rel.type === 'HAS_METHOD') {
        methodToClass.set(rel.end_node_id, rel.start_node_id);
      }
    }

    // Build call adjacency
    const callAdj = new Map<string, string[]>();
    for (const rel of relationships) {
      if (rel.type === 'CALLS') {
        const list = callAdj.get(rel.start_node_id) || [];
        list.push(rel.end_node_id);
        callAdj.set(rel.start_node_id, list);
      }
    }

    // DFS to build ordered call sequence
    const calls: SequenceCall[] = [];
    const visited = new Set<string>();

    const dfs = (methodId: string, depth: number) => {
      const key = methodId;
      if (visited.has(key)) return;
      visited.add(key);

      const targets = callAdj.get(methodId) || [];
      const callerNode = nodeMap.get(methodId);
      const callerClassId = methodToClass.get(methodId) || '';
      const callerClass = callerClassId ? nodeMap.get(callerClassId) : null;

      for (const targetId of targets) {
        const targetNode = nodeMap.get(targetId);
        const targetClassId = methodToClass.get(targetId) || '';
        const targetClass = targetClassId ? nodeMap.get(targetClassId) : null;

        calls.push({
          callerId: methodId,
          callerName: (callerNode?.properties['name'] as string) || '?',
          callerClassId,
          callerClassName: (callerClass?.properties['name'] as string) || '?',
          targetId,
          targetName: (targetNode?.properties['name'] as string) || '?',
          targetClassId,
          targetClassName: (targetClass?.properties['name'] as string) || '?',
          depth,
        });

        dfs(targetId, depth + 1);
      }
    };

    dfs(rootMethodId, 0);
    this.calls = calls;

    // Determine lane order: root class first, then in order of appearance
    const laneOrder: string[] = [];
    const rootClassId = methodToClass.get(rootMethodId) || '';
    if (rootClassId) laneOrder.push(rootClassId);
    for (const call of calls) {
      if (!laneOrder.includes(call.callerClassId)) laneOrder.push(call.callerClassId);
      if (!laneOrder.includes(call.targetClassId)) laneOrder.push(call.targetClassId);
    }

    // Create lanes
    const totalWidth = laneOrder.length * LANE_WIDTH;
    const startX = -totalWidth / 2 + LANE_WIDTH / 2;

    for (let i = 0; i < laneOrder.length; i++) {
      const classId = laneOrder[i];
      const classNode = nodeMap.get(classId);
      const className = (classNode?.properties['name'] as string) || '?';
      const sourceCode = (classNode?.properties['source_code'] as string) || '';

      // Collect methods belonging to this class
      const methods: { id: string; name: string; startLine: number; endLine: number }[] = [];
      for (const [methId, clsId] of methodToClass) {
        if (clsId === classId) {
          const methNode = nodeMap.get(methId);
          if (methNode) {
            methods.push({
              id: methId,
              name: (methNode.properties['name'] as string) || '?',
              startLine: (methNode.properties['start_line'] as number) ?? -1,
              endLine: (methNode.properties['end_line'] as number) ?? -1,
            });
          }
        }
      }

      const x = startX + i * LANE_WIDTH;
      this.createLane(classId, className, sourceCode, methods, x, calls.length);
    }

    // Create call arrows
    const laneX = new Map<string, number>();
    for (const lane of this.lanes) laneX.set(lane.classId, lane.x);

    for (let i = 0; i < calls.length; i++) {
      const call = calls[i];
      const y = -TOP_MARGIN - HEADER_HEIGHT - (i + 1) * ROW_HEIGHT;
      this.createCallArrow(call, laneX, y, i);
    }

    this.fitCamera();
  }

  clear() {
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
    this.lanes = [];
    this.callArrows = [];
    this.allMeshes = [];
    this.calls = [];
    this.hoveredArrow = null;
    this.selectedArrow = null;
    this.selectionBorder = null;
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
    if (w > 0 && h > 0) {
      this.renderer.setSize(w, h);
      this.fitCamera();
    }
  }

  getSourceInfo(classId: string): { className: string; sourceCode: string; methods: { id: string; name: string; startLine: number; endLine: number }[] } | null {
    const lane = this.lanes.find(l => l.classId === classId);
    if (!lane) return null;
    return {
      className: lane.className,
      sourceCode: lane.sourceCode,
      methods: lane.methods,
    };
  }

  // ----- Create elements -----

  private createLane(
    classId: string, className: string, sourceCode: string,
    methods: { id: string; name: string; startLine: number; endLine: number }[],
    x: number, callCount: number,
  ) {
    // Header
    const headerTex = makeTexture(HEADER_WIDTH, HEADER_HEIGHT, (ctx, w, h) => {
      roundedRect(ctx, 0, 0, w, h, CORNER_RADIUS);
      ctx.fillStyle = `#${COLOR_HEADER_BG.toString(16).padStart(6, '0')}`;
      ctx.fill();

      ctx.font = FONT_HEADER;
      ctx.fillStyle = COLOR_HEADER_TEXT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const displayName = className.length > 20 ? className.substring(0, 18) + '...' : className;
      ctx.fillText(displayName, w / 2, h / 2);
    });

    const headerGeo = new THREE.PlaneGeometry(HEADER_WIDTH, HEADER_HEIGHT);
    const headerMat = new THREE.MeshBasicMaterial({ map: headerTex, transparent: true });
    const headerMesh = new THREE.Mesh(headerGeo, headerMat);
    headerMesh.position.set(x, -TOP_MARGIN, 1);
    headerMesh.userData = { type: 'header', classId };
    this.scene.add(headerMesh);
    this.allMeshes.push(headerMesh);

    // Lifeline
    const lifelineLength = (callCount + 2) * ROW_HEIGHT;
    const lifelineGeo = new THREE.BufferGeometry();
    const lifelineStart = -TOP_MARGIN - HEADER_HEIGHT / 2;
    const lifelineEnd = lifelineStart - lifelineLength;
    const positions = new Float32Array([
      x, lifelineStart, 0,
      x, lifelineEnd, 0,
    ]);
    lifelineGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const lifelineMat = new THREE.LineDashedMaterial({
      color: COLOR_LIFELINE,
      dashSize: 6,
      gapSize: 4,
      linewidth: 1,
    });
    const lifeline = new THREE.Line(lifelineGeo, lifelineMat);
    lifeline.computeLineDistances();
    this.scene.add(lifeline);

    this.lanes.push({
      classId, className, x, headerMesh, lifeline, sourceCode, methods,
    });
  }

  private createCallArrow(
    call: SequenceCall,
    laneX: Map<string, number>,
    y: number,
    index: number,
  ) {
    const srcX = laneX.get(call.callerClassId) ?? 0;
    const tgtX = laneX.get(call.targetClassId) ?? 0;
    const isSelfCall = call.callerClassId === call.targetClassId;

    if (isSelfCall) {
      this.createSelfCallArrow(call, srcX, y, index);
    } else {
      this.createStraightArrow(call, srcX, tgtX, y, index);
    }
  }

  private createStraightArrow(
    call: SequenceCall,
    srcX: number, tgtX: number, y: number,
    index: number,
  ) {
    // Line
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array([srcX, y, 2, tgtX, y, 2]), 3));
    const lineMat = new THREE.LineBasicMaterial({ color: COLOR_ARROW });
    const line = new THREE.Line(lineGeo, lineMat);
    // Store line as userData on the mesh so we can manipulate later
    this.scene.add(line);

    // Arrowhead
    const dir = tgtX > srcX ? 1 : -1;
    const arrowGeo = new THREE.BufferGeometry();
    arrowGeo.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array([
        tgtX, y, 2,
        tgtX - dir * ARROW_HEAD_SIZE, y + ARROW_HEAD_SIZE / 2, 2,
        tgtX - dir * ARROW_HEAD_SIZE, y - ARROW_HEAD_SIZE / 2, 2,
      ]), 3));
    arrowGeo.setIndex([0, 1, 2]);
    const arrowMat = new THREE.MeshBasicMaterial({ color: COLOR_ARROW });
    const arrowHead = new THREE.Mesh(arrowGeo, arrowMat);
    this.scene.add(arrowHead);

    // Label
    const label = call.targetName === '<init>'
      ? `new ${call.targetClassName}()`
      : `${call.targetName}()`;
    const labelWidth = measureText(label, FONT_LABEL) + 12;
    const labelHeight = 16;
    const labelTex = makeTexture(labelWidth, labelHeight, (ctx, w, h) => {
      ctx.font = FONT_LABEL;
      ctx.fillStyle = COLOR_LABEL_TEXT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, w / 2, h / 2);
    });
    const labelGeo = new THREE.PlaneGeometry(labelWidth, labelHeight);
    const labelMat = new THREE.MeshBasicMaterial({ map: labelTex, transparent: true });
    const labelMesh = new THREE.Mesh(labelGeo, labelMat);
    labelMesh.position.set((srcX + tgtX) / 2, y + labelHeight / 2 + 2, 3);
    labelMesh.userData = { type: 'callLabel', index };
    this.scene.add(labelMesh);
    this.allMeshes.push(labelMesh);

    // Clickable line overlay (thin mesh for raycasting)
    const overlayWidth = Math.abs(tgtX - srcX);
    const overlayGeo = new THREE.PlaneGeometry(overlayWidth, 12);
    const overlayMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0 });
    const overlayMesh = new THREE.Mesh(overlayGeo, overlayMat);
    overlayMesh.position.set((srcX + tgtX) / 2, y, 4);
    overlayMesh.userData = { type: 'callOverlay', index };
    this.scene.add(overlayMesh);
    this.allMeshes.push(overlayMesh);

    this.callArrows.push({
      mesh: overlayMesh,
      labelMesh,
      arrowHead,
      call,
      y,
    });

    // Store line reference for hover coloring
    overlayMesh.userData['line'] = line;
    overlayMesh.userData['arrowHead'] = arrowHead;
  }

  private createSelfCallArrow(
    call: SequenceCall,
    x: number, y: number,
    index: number,
  ) {
    const group = new THREE.Group();
    this.scene.add(group);

    // U-shape: right, down, left
    const rightX = x + SELF_CALL_OFFSET;
    const bottomY = y - ROW_HEIGHT * 0.4;

    const points = [
      new THREE.Vector3(x, y, 2),
      new THREE.Vector3(rightX, y, 2),
      new THREE.Vector3(rightX, bottomY, 2),
      new THREE.Vector3(x, bottomY, 2),
    ];

    const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
    const lineMat = new THREE.LineBasicMaterial({ color: COLOR_ARROW_SELF });
    const line = new THREE.Line(lineGeo, lineMat);
    group.add(line);

    // Arrowhead pointing left at return
    const arrowGeo = new THREE.BufferGeometry();
    arrowGeo.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array([
        x, bottomY, 2,
        x + ARROW_HEAD_SIZE, bottomY + ARROW_HEAD_SIZE / 2, 2,
        x + ARROW_HEAD_SIZE, bottomY - ARROW_HEAD_SIZE / 2, 2,
      ]), 3));
    arrowGeo.setIndex([0, 1, 2]);
    const arrowMat = new THREE.MeshBasicMaterial({ color: COLOR_ARROW_SELF });
    const arrowHead = new THREE.Mesh(arrowGeo, arrowMat);
    group.add(arrowHead);

    // Label
    const label = call.targetName === '<init>'
      ? `new ${call.targetClassName}()`
      : `${call.targetName}()`;
    const labelWidth = measureText(label, FONT_LABEL) + 12;
    const labelHeight = 16;
    const labelTex = makeTexture(labelWidth, labelHeight, (ctx, w, h) => {
      ctx.font = FONT_LABEL;
      ctx.fillStyle = COLOR_LABEL_TEXT;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, 4, h / 2);
    });
    const labelGeo = new THREE.PlaneGeometry(labelWidth, labelHeight);
    const labelMat = new THREE.MeshBasicMaterial({ map: labelTex, transparent: true });
    const labelMesh = new THREE.Mesh(labelGeo, labelMat);
    labelMesh.position.set(rightX + labelWidth / 2 + 4, y, 3);
    labelMesh.userData = { type: 'callLabel', index };
    this.scene.add(labelMesh);
    this.allMeshes.push(labelMesh);

    // Clickable overlay
    const overlayGeo = new THREE.PlaneGeometry(SELF_CALL_OFFSET + 20, ROW_HEIGHT * 0.6);
    const overlayMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0 });
    const overlayMesh = new THREE.Mesh(overlayGeo, overlayMat);
    overlayMesh.position.set(x + SELF_CALL_OFFSET / 2, (y + bottomY) / 2, 4);
    overlayMesh.userData = { type: 'callOverlay', index };
    this.scene.add(overlayMesh);
    this.allMeshes.push(overlayMesh);

    overlayMesh.userData['line'] = line;
    overlayMesh.userData['arrowHead'] = arrowHead;
    overlayMesh.userData['selfLoopGroup'] = group;

    this.callArrows.push({
      mesh: overlayMesh,
      labelMesh,
      arrowHead,
      selfLoopGroup: group,
      call,
      y,
    });
  }

  // ----- Camera -----

  private fitCamera() {
    if (this.lanes.length === 0) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    for (const lane of this.lanes) {
      minX = Math.min(minX, lane.x - HEADER_WIDTH / 2);
      maxX = Math.max(maxX, lane.x + HEADER_WIDTH / 2);
    }
    minY = -TOP_MARGIN - HEADER_HEIGHT - (this.calls.length + 1) * ROW_HEIGHT;
    maxY = -TOP_MARGIN + HEADER_HEIGHT;

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = maxX - minX + 100;
    const h = maxY - minY + 100;

    const container = this.container;
    const aspect = (container.clientWidth || 800) / (container.clientHeight || 600);
    const viewW = w / 2;
    const viewH = h / 2;
    const view = Math.max(viewW / aspect, viewH);

    this.camera.left = -view * aspect;
    this.camera.right = view * aspect;
    this.camera.top = view;
    this.camera.bottom = -view;
    this.camera.position.set(cx, cy, 100);
    this.camera.lookAt(cx, cy, 0);
    this.camera.updateProjectionMatrix();
  }

  // ----- Interaction -----

  private screenToWorld(screenX: number, screenY: number): THREE.Vector2 {
    const rect = this.container.getBoundingClientRect();
    const ndcX = ((screenX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((screenY - rect.top) / rect.height) * 2 + 1;
    const worldX = this.camera.position.x + ndcX * (this.camera.right - this.camera.left) / 2;
    const worldY = this.camera.position.y + ndcY * (this.camera.top - this.camera.bottom) / 2;
    return new THREE.Vector2(worldX, worldY);
  }

  private hitTest(event: MouseEvent): THREE.Mesh | null {
    const rect = this.container.getBoundingClientRect();
    this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.mouse, this.camera);
    const intersects = this.raycaster.intersectObjects(this.allMeshes, false);
    return intersects.length > 0 ? intersects[0].object as THREE.Mesh : null;
  }

  private findArrowByIndex(index: number): CallArrowEntry | null {
    return this.callArrows[index] || null;
  }

  private setArrowHover(arrow: CallArrowEntry | null) {
    // Unhover previous
    if (this.hoveredArrow && this.hoveredArrow !== arrow) {
      const line = this.hoveredArrow.mesh.userData['line'] as THREE.Line;
      const head = this.hoveredArrow.mesh.userData['arrowHead'] as THREE.Mesh;
      const isSelf = !!this.hoveredArrow.selfLoopGroup;
      const color = isSelf ? COLOR_ARROW_SELF : COLOR_ARROW;
      (line.material as THREE.LineBasicMaterial).color.setHex(color);
      (head.material as THREE.MeshBasicMaterial).color.setHex(color);
    }

    this.hoveredArrow = arrow;

    if (arrow) {
      const line = arrow.mesh.userData['line'] as THREE.Line;
      const head = arrow.mesh.userData['arrowHead'] as THREE.Mesh;
      (line.material as THREE.LineBasicMaterial).color.setHex(COLOR_ARROW_HOVER);
      (head.material as THREE.MeshBasicMaterial).color.setHex(COLOR_ARROW_HOVER);
      this.container.style.cursor = 'pointer';
    } else {
      this.container.style.cursor = 'default';
    }
  }

  // ----- Event handlers -----

  private onMouseDown = (e: MouseEvent) => {
    if (e.button !== 0) return;
    this.isDragging = false;
    this.lastMouse = { x: e.clientX, y: e.clientY };
    this.mouseDownPos = { x: e.clientX, y: e.clientY };
  };

  private onMouseMove = (e: MouseEvent) => {
    if (e.buttons & 1) {
      const dx = e.clientX - this.lastMouse.x;
      const dy = e.clientY - this.lastMouse.y;

      if (Math.abs(e.clientX - this.mouseDownPos.x) > 3 ||
          Math.abs(e.clientY - this.mouseDownPos.y) > 3) {
        this.isDragging = true;
      }

      const scaleX = (this.camera.right - this.camera.left) / this.container.clientWidth;
      const scaleY = (this.camera.top - this.camera.bottom) / this.container.clientHeight;
      this.camera.position.x -= dx * scaleX;
      this.camera.position.y += dy * scaleY;
      this.camera.updateProjectionMatrix();

      this.lastMouse = { x: e.clientX, y: e.clientY };
      return;
    }

    // Hover detection
    const hit = this.hitTest(e);
    if (hit) {
      const type = hit.userData['type'];
      if (type === 'callOverlay' || type === 'callLabel') {
        const arrow = this.findArrowByIndex(hit.userData['index']);
        this.setArrowHover(arrow);
      } else if (type === 'header') {
        this.setArrowHover(null);
        this.container.style.cursor = 'pointer';
      } else {
        this.setArrowHover(null);
      }
    } else {
      this.setArrowHover(null);
    }
  };

  private onMouseUp = (_e: MouseEvent) => {
    // handled by onClick
  };

  private onClick = (e: MouseEvent) => {
    if (this.isDragging) return;
    const hit = this.hitTest(e);
    if (!hit) return;

    const type = hit.userData['type'];
    if (type === 'callOverlay' || type === 'callLabel') {
      const arrow = this.findArrowByIndex(hit.userData['index']);
      if (arrow) {
        this.selectedArrow = arrow;
        this.onCallClick?.(arrow.call);
      }
    } else if (type === 'header') {
      this.onHeaderClick?.(hit.userData['classId']);
    }
  };

  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.1 : 0.9;

    const rect = this.container.getBoundingClientRect();
    const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((e.clientY - rect.top) / rect.height) * 2 + 1;

    const worldBefore = new THREE.Vector2(
      this.camera.position.x + ndcX * (this.camera.right - this.camera.left) / 2,
      this.camera.position.y + ndcY * (this.camera.top - this.camera.bottom) / 2,
    );

    const halfW = (this.camera.right - this.camera.left) / 2 * factor;
    const halfH = (this.camera.top - this.camera.bottom) / 2 * factor;
    this.camera.left = -halfW;
    this.camera.right = halfW;
    this.camera.top = halfH;
    this.camera.bottom = -halfH;

    const worldAfter = new THREE.Vector2(
      this.camera.position.x + ndcX * halfW,
      this.camera.position.y + ndcY * halfH,
    );

    this.camera.position.x += worldBefore.x - worldAfter.x;
    this.camera.position.y += worldBefore.y - worldAfter.y;
    this.camera.updateProjectionMatrix();
  };

  private onContextMenu = (e: MouseEvent) => {
    e.preventDefault();
    const hit = this.hitTest(e);
    if (!hit) return;

    const type = hit.userData['type'];
    if (type === 'callOverlay' || type === 'callLabel') {
      const arrow = this.findArrowByIndex(hit.userData['index']);
      if (arrow) this.onCallRightClick?.(arrow.call, e.clientX, e.clientY);
    } else if (type === 'header') {
      this.onHeaderRightClick?.(hit.userData['classId'], e.clientX, e.clientY);
    }
  };

  private onResize = () => {
    this.resize();
  };

  // ----- Render loop -----

  private animate = () => {
    this.animFrameId = requestAnimationFrame(this.animate);
    this.renderer.render(this.scene, this.camera);
  };
}
