import { GraphLayout, LayoutNode, LayoutEdge } from './layout';

const MIN_WIDTH = 180;
const HEADER_H = 28;
const H_GAP = 60;
const V_GAP = 40;

export class TreeLayout implements GraphLayout {
  name = 'tree';

  layout(nodes: LayoutNode[], edges: LayoutEdge[]): Map<string, { x: number; y: number }> {
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const positions = new Map<string, { x: number; y: number }>();

    // Build call graph (CALLS edges only)
    const callsOut = new Map<string, string[]>();
    const callsInCount = new Map<string, number>();
    for (const n of nodes) {
      callsOut.set(n.id, []);
      callsInCount.set(n.id, 0);
    }

    const DIRECTED_TYPES = new Set(['CALLS', 'HAS_ENDPOINT', 'LISTENS_ON', 'SENDS_TO']);
    const seenEdge = new Set<string>();
    for (const edge of edges) {
      if (!DIRECTED_TYPES.has(edge.type)) continue;
      if (edge.sourceId === edge.targetId) continue;
      if (!nodeMap.has(edge.sourceId) || !nodeMap.has(edge.targetId)) continue;
      const key = `${edge.sourceId}->${edge.targetId}`;
      if (seenEdge.has(key)) continue;
      seenEdge.add(key);
      callsOut.get(edge.sourceId)!.push(edge.targetId);
      callsInCount.set(edge.targetId, (callsInCount.get(edge.targetId) || 0) + 1);
    }

    // Sort children: more outgoing calls first (deeper subtree heuristic)
    for (const [, children] of callsOut) {
      children.sort((a, b) => (callsOut.get(b)?.length || 0) - (callsOut.get(a)?.length || 0));
    }

    // Find roots (no incoming CALLS edges)
    const roots: string[] = [];
    for (const [id, count] of callsInCount) {
      if (count === 0) roots.push(id);
    }
    roots.sort((a, b) => (callsOut.get(b)?.length || 0) - (callsOut.get(a)?.length || 0));

    // DFS tree layout: first child same row, subsequent children on new rows
    const visited = new Set<string>();
    const gridPositions = new Map<string, { col: number; row: number }>();
    let nextRow = 0;

    const layoutNode = (id: string, col: number) => {
      if (visited.has(id)) return;
      visited.add(id);
      gridPositions.set(id, { col, row: nextRow });
      const children = (callsOut.get(id) || []).filter(c => !visited.has(c));
      for (let i = 0; i < children.length; i++) {
        if (i > 0) nextRow++;
        layoutNode(children[i], col + 1);
      }
    };

    for (const root of roots) {
      if (visited.has(root)) continue;
      layoutNode(root, 0);
      nextRow++;
    }

    // Remaining unvisited nodes
    for (const n of nodes) {
      if (!visited.has(n.id)) {
        gridPositions.set(n.id, { col: 0, row: nextRow++ });
      }
    }

    if (gridPositions.size === 0) return positions;

    // Compute column widths and row heights
    const colWidths = new Map<number, number>();
    const rowHeights = new Map<number, number>();
    for (const [id, pos] of gridPositions) {
      const node = nodeMap.get(id)!;
      colWidths.set(pos.col, Math.max(colWidths.get(pos.col) || 0, node.width));
      rowHeights.set(pos.row, Math.max(rowHeights.get(pos.row) || 0, node.height));
    }

    // Column left edges
    const colLeft = new Map<number, number>();
    let cx = 0;
    const maxCol = Math.max(...colWidths.keys());
    for (let c = 0; c <= maxCol; c++) {
      colLeft.set(c, cx);
      cx += (colWidths.get(c) || MIN_WIDTH) + H_GAP;
    }

    // Row top edges
    const rowTopY = new Map<number, number>();
    let ry = 0;
    const maxRow = Math.max(...rowHeights.keys());
    for (let r = 0; r <= maxRow; r++) {
      rowTopY.set(r, -ry);
      ry += (rowHeights.get(r) || HEADER_H) + V_GAP;
    }

    // Convert grid to pixel positions (left-aligned, top-aligned)
    for (const [id, pos] of gridPositions) {
      const node = nodeMap.get(id)!;
      positions.set(id, {
        x: (colLeft.get(pos.col) || 0) + node.width / 2,
        y: rowTopY.get(pos.row) || 0,
      });
    }

    return positions;
  }
}
