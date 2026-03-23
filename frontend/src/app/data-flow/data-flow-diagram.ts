import { Component, input, output, computed, signal, ElementRef, viewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { ServiceDetail, EndpointGroup, EndpointItem } from '../services/data-flow';

interface Rect { x: number; y: number; w: number; h: number; }
interface IdRect extends Rect { id: string; }

interface GroupBox extends IdRect {
  group: EndpointGroup;
  epRects: IdRect[];
}

interface QueueBox extends IdRect {
  name: string; type: string; direction: string;
}

interface DbBox extends IdRect {
  name: string; technology: string;
}

interface Arrow {
  from: IdRect; to: IdRect;
  color: string; dashed: boolean;
}

interface Layout {
  contentW: number; contentH: number;
  boundaryX: number; boundaryY: number;
  boundaryW: number; boundaryH: number;
  serviceBox: IdRect;
  inGroups: GroupBox[];
  outGroups: GroupBox[];
  queues: QueueBox[];
  databases: DbBox[];
  arrows: Arrow[];
}

const EP_W = 180;
const EP_H = 26;
const EP_GAP = 5;
const GRP_PAD = 8;
const GRP_HEADER = 22;
const GRP_GAP = 14;
const SVC_W = 140;
const SVC_H = 48;
const Q_W = 130;
const Q_H = 28;
const DB_W = 130;
const DB_H = 34;
const PAD = 30;
const BPAD = 20;
const ZONE_LABEL = 20;
const COL_GAP = 50;

@Component({
  selector: 'app-data-flow-diagram',
  standalone: true,
  imports: [],
  templateUrl: './data-flow-diagram.html',
  styleUrl: './data-flow-diagram.scss',
})
export class DataFlowDiagram implements AfterViewInit, OnDestroy {
  detail = input.required<ServiceDetail>();
  epClick = output<EndpointItem>();

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

  layout = computed<Layout>(() => {
    const d = this.detail();

    // Build group boxes starting at a given Y
    const buildGroups = (groups: EndpointGroup[], startX: number, startY: number, prefix: string): GroupBox[] => {
      let y = startY;
      return groups.map((g, gi) => {
        const epRects: IdRect[] = g.endpoints.map((ep, ei) => ({
          x: startX + GRP_PAD,
          y: y + GRP_HEADER + ei * (EP_H + EP_GAP),
          w: EP_W, h: EP_H,
          id: `${prefix}-${gi}-ep-${ei}`,
        }));
        const boxH = GRP_HEADER + g.endpoints.length * (EP_H + EP_GAP) - EP_GAP + GRP_PAD;
        const box: GroupBox = {
          x: startX, y, w: EP_W + GRP_PAD * 2, h: boxH,
          id: `${prefix}-${gi}`, group: g, epRects,
        };
        y += boxH + GRP_GAP;
        return box;
      });
    };

    // Service box at top; groups below it
    const groupsStartY = PAD + BPAD + SVC_H + 30 + ZONE_LABEL;

    const inX = PAD + BPAD;
    const inGroups = buildGroups(d.inbound_groups, inX, groupsStartY, 'in');

    const outX = inX + (d.inbound_groups.length ? EP_W + GRP_PAD * 2 + COL_GAP : 0);
    const outGroups = buildGroups(d.outbound_groups, outX, groupsStartY, 'out');

    // Center service box across both columns
    const rightEdge = Math.max(
      outGroups.length ? outX + EP_W + GRP_PAD * 2 : 0,
      inX + EP_W + GRP_PAD * 2,
    );
    const centerX = PAD + BPAD + (rightEdge - PAD - BPAD) / 2;
    const serviceBox: IdRect = {
      x: centerX - SVC_W / 2, y: PAD + BPAD,
      w: SVC_W, h: SVC_H, id: 'svc',
    };

    // Column bottoms
    const lastIn = inGroups[inGroups.length - 1];
    const lastOut = outGroups[outGroups.length - 1];
    const colBottom = Math.max(
      lastIn ? lastIn.y + lastIn.h : groupsStartY,
      lastOut ? lastOut.y + lastOut.h : groupsStartY,
    );

    // Bottom row (queues + databases)
    const bottomY = colBottom + 30 + ZONE_LABEL;
    const bottomItems = d.queues.length + d.databases.length;
    const totalBottomW = d.queues.length * (Q_W + 10) + d.databases.length * (DB_W + 10) - (bottomItems > 0 ? 10 : 0);
    let bx = centerX - totalBottomW / 2;

    const queues: QueueBox[] = d.queues.map((q, i) => {
      const box: QueueBox = { x: bx, y: bottomY, w: Q_W, h: Q_H, id: `q-${i}`, ...q };
      bx += Q_W + 10;
      return box;
    });
    const databases: DbBox[] = d.databases.map((db, i) => {
      const box: DbBox = { x: bx, y: bottomY, w: DB_W, h: DB_H, id: `db-${i}`, name: db.name, technology: db.technology };
      bx += DB_W + 10;
      return box;
    });

    // Boundary
    const allRects: Rect[] = [serviceBox, ...inGroups, ...outGroups, ...queues, ...databases];
    const bRight = allRects.reduce((a, r) => Math.max(a, r.x + r.w), 0) + BPAD;
    const bBottom = allRects.reduce((a, r) => Math.max(a, r.y + r.h), 0) + BPAD;

    // Arrows: controllers → outbound calls, queues, databases
    const arrows: Arrow[] = [];
    const sources = inGroups.length ? inGroups : [serviceBox];
    for (const src of sources) {
      for (const og of outGroups) {
        arrows.push({ from: src, to: og, color: '#845ef7', dashed: false });
      }
      for (const q of queues) {
        arrows.push({ from: src, to: q, color: '#20c997', dashed: true });
      }
      for (const db of databases) {
        arrows.push({ from: src, to: db, color: '#22b8cf', dashed: false });
      }
    }

    const contentW = bRight + PAD;
    const contentH = bBottom + PAD;

    return {
      contentW, contentH,
      boundaryX: PAD, boundaryY: PAD,
      boundaryW: bRight - PAD, boundaryH: bBottom - PAD,
      serviceBox, inGroups, outGroups, queues, databases, arrows,
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

  epLabel(ep: EndpointItem): string {
    const l = ep.http_method ? `${ep.http_method} ${ep.path}` : ep.path;
    return l.length > 26 ? l.substring(0, 23) + '...' : l;
  }

  epTooltip(ep: EndpointItem): string {
    return ep.http_method ? `${ep.http_method} ${ep.path}` : ep.path;
  }

  arrowPath(a: Arrow): string {
    const fromRight = a.from.x + a.from.w;
    const toLeft = a.to.x;

    // Target is in a column to the right → horizontal
    if (toLeft > fromRight + 10) {
      const fx = fromRight, fy = a.from.y + a.from.h / 2;
      const tx = toLeft,    ty = a.to.y + a.to.h / 2;
      return `M${fx},${fy} L${tx},${ty}`;
    }

    // Target is below → vertical
    const fx = a.from.x + a.from.w / 2, fy = a.from.y + a.from.h;
    const tx = a.to.x + a.to.w / 2,     ty = a.to.y;
    return `M${fx},${fy} L${tx},${ty}`;
  }

  markerId(color: string): string {
    const m: Record<string, string> = {
      '#339af0': 'arr-blue', '#845ef7': 'arr-purple',
      '#20c997': 'arr-teal', '#22b8cf': 'arr-cyan',
    };
    return m[color] || 'arr-blue';
  }

  // ── Hover ────────────────────────────────────────────────

  onNodeEnter(id: string) {
    this.hoveredId.set(id);
    const linked = new Set<string>([id]);
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
