import { Component, input, computed, signal, ElementRef, viewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { EndpointFlowDetail, EndpointGroup, DataModelInfo, DatabaseWithRepos, QueueItem } from '../services/data-flow';

interface Rect { x: number; y: number; w: number; h: number; }
interface IdRect extends Rect { id: string; }

interface ModelBox extends IdRect {
  model: DataModelInfo;
}

interface OutGroupBox extends IdRect {
  group: EndpointGroup;
  epRects: IdRect[];
}

interface DbDetailBox extends IdRect {
  db: DatabaseWithRepos;
  repoLines: string[];
}

interface QueueBox extends IdRect {
  name: string; type: string; direction: string;
}

interface Arrow {
  from: IdRect; to: IdRect;
  color: string; dashed: boolean;
}

interface FlowLayout {
  contentW: number; contentH: number;
  epBox: IdRect;
  reqModels: ModelBox[];
  resModels: ModelBox[];
  outGroups: OutGroupBox[];
  databases: DbDetailBox[];
  queues: QueueBox[];
  arrows: Arrow[];
}

const PAD = 30;
const EP_BOX_W = 320;
const EP_BOX_H = 44;
const MDL_W = 180;
const MDL_H = 36;
const MDL_GAP = 8;
const OUT_EP_W = 200;
const OUT_EP_H = 24;
const OUT_EP_GAP = 4;
const OUT_GRP_PAD = 8;
const OUT_GRP_HEADER = 20;
const OUT_GRP_GAP = 12;
const DB_W = 200;
const DB_REPO_H = 18;
const DB_HEADER = 24;
const DB_PAD = 8;
const Q_W = 130;
const Q_H = 28;
const ROW_GAP = 50;
const COL_GAP = 30;

@Component({
  selector: 'app-data-flow-endpoint',
  standalone: true,
  imports: [],
  templateUrl: './data-flow-endpoint.html',
  styleUrl: './data-flow-endpoint.scss',
})
export class DataFlowEndpoint implements AfterViewInit, OnDestroy {
  detail = input.required<EndpointFlowDetail>();

  hoveredId = signal<string | null>(null);
  hoveredLinks = signal<Set<string>>(new Set());

  vbX = signal(0);
  vbY = signal(0);
  vbW = signal(800);
  vbH = signal(600);
  private isDragging = false;
  private lastMouse = { x: 0, y: 0 };

  svgEl = viewChild<ElementRef<SVGSVGElement>>('svgEl');
  currentViewBox = computed(() =>
    `${this.vbX()} ${this.vbY()} ${this.vbW()} ${this.vbH()}`);

  layout = computed<FlowLayout>(() => {
    const d = this.detail();

    // Row 0: Endpoint box centered at top
    const epBox: IdRect = { x: PAD, y: PAD, w: EP_BOX_W, h: EP_BOX_H, id: 'ep' };

    // Row 1: Request models (left) + Response models (right)
    const modelsY = PAD + EP_BOX_H + ROW_GAP;
    const reqModels: ModelBox[] = d.request_models.map((m, i) => ({
      x: PAD, y: modelsY + i * (MDL_H + MDL_GAP),
      w: MDL_W, h: MDL_H, id: `req-${i}`, model: m,
    }));
    const resModels: ModelBox[] = d.response_models.map((m, i) => ({
      x: PAD + MDL_W + COL_GAP, y: modelsY + i * (MDL_H + MDL_GAP),
      w: MDL_W, h: MDL_H, id: `res-${i}`, model: m,
    }));

    const modelsBottom = Math.max(
      reqModels.length ? reqModels[reqModels.length - 1].y + MDL_H : modelsY,
      resModels.length ? resModels[resModels.length - 1].y + MDL_H : modelsY,
    );

    // Row 2: Outbound groups
    const outStartY = modelsBottom + ROW_GAP;
    let outX = PAD;
    const outGroups: OutGroupBox[] = d.outbound_groups.map((g, gi) => {
      const epRects: IdRect[] = g.endpoints.map((ep, ei) => ({
        x: outX + OUT_GRP_PAD,
        y: outStartY + OUT_GRP_HEADER + ei * (OUT_EP_H + OUT_EP_GAP),
        w: OUT_EP_W, h: OUT_EP_H,
        id: `out-${gi}-ep-${ei}`,
      }));
      const boxH = OUT_GRP_HEADER + g.endpoints.length * (OUT_EP_H + OUT_EP_GAP) - OUT_EP_GAP + OUT_GRP_PAD;
      const box: OutGroupBox = {
        x: outX, y: outStartY,
        w: OUT_EP_W + OUT_GRP_PAD * 2, h: boxH,
        id: `out-${gi}`, group: g, epRects,
      };
      outX += box.w + OUT_GRP_GAP;
      return box;
    });

    const outBottom = outGroups.length
      ? Math.max(...outGroups.map(g => g.y + g.h))
      : outStartY;

    // Row 3: Databases + Queues
    const storageY = (outGroups.length ? outBottom : modelsBottom) + ROW_GAP;
    let stX = PAD;

    const databases: DbDetailBox[] = d.databases.map((db, i) => {
      const repoLines = db.repositories.map(r =>
        r.entity_type ? `${r.name} (${r.entity_type})` : r.name);
      const boxH = DB_HEADER + repoLines.length * DB_REPO_H + DB_PAD;
      const box: DbDetailBox = {
        x: stX, y: storageY, w: DB_W, h: boxH,
        id: `db-${i}`, db, repoLines,
      };
      stX += DB_W + 12;
      return box;
    });

    const queues: QueueBox[] = d.queues.map((q, i) => {
      const box: QueueBox = {
        x: stX, y: storageY, w: Q_W, h: Q_H,
        id: `q-${i}`, ...q,
      };
      stX += Q_W + 12;
      return box;
    });

    // Reposition endpoint box to center over content
    const allRects: Rect[] = [epBox, ...reqModels, ...resModels, ...outGroups, ...databases, ...queues];
    const contentRight = allRects.reduce((a, r) => Math.max(a, r.x + r.w), 0);
    const totalW = contentRight - PAD;
    epBox.x = PAD + Math.max(0, (totalW - EP_BOX_W) / 2);

    // Also center models row
    const modelsRowW = (resModels.length ? MDL_W + COL_GAP + MDL_W : MDL_W);
    const modelsStartX = PAD + Math.max(0, (totalW - modelsRowW) / 2);
    for (const m of reqModels) { m.x = modelsStartX; }
    for (const m of resModels) { m.x = modelsStartX + MDL_W + COL_GAP; }

    // Arrows
    const arrows: Arrow[] = [];
    // Endpoint → request models
    for (const m of reqModels) {
      arrows.push({ from: epBox, to: m, color: '#fd7e14', dashed: false });
    }
    // Endpoint → response models
    for (const m of resModels) {
      arrows.push({ from: epBox, to: m, color: '#339af0', dashed: false });
    }
    // Endpoint → outbound groups
    for (const g of outGroups) {
      arrows.push({ from: epBox, to: g, color: '#845ef7', dashed: false });
    }
    // Endpoint → databases
    for (const db of databases) {
      arrows.push({ from: epBox, to: db, color: '#22b8cf', dashed: false });
    }
    // Endpoint → queues
    for (const q of queues) {
      arrows.push({ from: epBox, to: q, color: '#20c997', dashed: true });
    }

    const finalRight = allRects.reduce((a, r) => Math.max(a, r.x + r.w), 0) + PAD;
    const finalBottom = allRects.reduce((a, r) => Math.max(a, r.y + r.h), 0) + PAD;

    return {
      contentW: finalRight, contentH: finalBottom,
      epBox, reqModels, resModels, outGroups, databases, queues, arrows,
    };
  });

  ngAfterViewInit() {
    const l = this.layout();
    this.vbW.set(l.contentW);
    this.vbH.set(l.contentH);
  }
  ngOnDestroy() {}

  // ── Zoom / Pan ───────────────────────────────────────────

  onWheel(e: WheelEvent) {
    e.preventDefault();
    const svg = this.svgEl()?.nativeElement;
    if (!svg) return;
    const factor = e.deltaY > 0 ? 1.1 : 1 / 1.1;
    const rect = svg.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width;
    const my = (e.clientY - rect.top) / rect.height;
    const sx = this.vbX() + mx * this.vbW();
    const sy = this.vbY() + my * this.vbH();
    const nw = this.vbW() * factor;
    const nh = this.vbH() * factor;
    this.vbX.set(sx - mx * nw);
    this.vbY.set(sy - my * nh);
    this.vbW.set(nw);
    this.vbH.set(nh);
  }

  onMouseDown(e: MouseEvent) {
    if (e.button === 0) { this.isDragging = true; this.lastMouse = { x: e.clientX, y: e.clientY }; }
  }
  onMouseMove(e: MouseEvent) {
    if (!this.isDragging) return;
    const svg = this.svgEl()?.nativeElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const dx = e.clientX - this.lastMouse.x;
    const dy = e.clientY - this.lastMouse.y;
    this.lastMouse = { x: e.clientX, y: e.clientY };
    this.vbX.update(v => v - dx * this.vbW() / rect.width);
    this.vbY.update(v => v - dy * this.vbH() / rect.height);
  }
  onMouseUp() { this.isDragging = false; }

  resetView() {
    const l = this.layout();
    this.vbX.set(0); this.vbY.set(0);
    this.vbW.set(l.contentW); this.vbH.set(l.contentH);
  }

  // ── Helpers ──────────────────────────────────────────────

  epLabel(path: string, method: string): string {
    const l = method ? `${method} ${path}` : path;
    return l.length > 30 ? l.substring(0, 27) + '...' : l;
  }

  arrowPath(a: Arrow): string {
    const fx = a.from.x + a.from.w / 2, fy = a.from.y + a.from.h;
    const tx = a.to.x + a.to.w / 2,     ty = a.to.y;
    return `M${fx},${fy} L${tx},${ty}`;
  }

  markerId(color: string): string {
    const m: Record<string, string> = {
      '#fd7e14': 'arr-orange', '#339af0': 'arr-blue', '#845ef7': 'arr-purple',
      '#20c997': 'arr-teal', '#22b8cf': 'arr-cyan',
    };
    return m[color] || 'arr-blue';
  }

  // ── Hover ────────────────────────────────────────────────

  onNodeEnter(id: string) {
    this.hoveredId.set(id);
    const linked = new Set<string>([id, 'ep']);
    for (const a of this.layout().arrows) {
      if (a.from.id === id || a.to.id === id) { linked.add(a.from.id); linked.add(a.to.id); }
    }
    this.hoveredLinks.set(linked);
  }
  onNodeLeave() { this.hoveredId.set(null); this.hoveredLinks.set(new Set()); }
  isDimmed(id: string): boolean { const h = this.hoveredId(); return h !== null && !this.hoveredLinks().has(id); }
  isArrowDimmed(a: Arrow): boolean { const h = this.hoveredId(); return h !== null && a.from.id !== h && a.to.id !== h; }
  isArrowHit(a: Arrow): boolean { const h = this.hoveredId(); return h !== null && (a.from.id === h || a.to.id === h); }
}
