export interface LayoutNode {
  id: string;
  width: number;
  height: number;
}

export interface LayoutEdge {
  sourceId: string;
  targetId: string;
  type: string;
}

export interface GraphLayout {
  name: string;
  layout(nodes: LayoutNode[], edges: LayoutEdge[]): Map<string, { x: number; y: number }>;
}
