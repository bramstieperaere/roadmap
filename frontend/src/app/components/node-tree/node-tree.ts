import { Component, inject, signal, input, output, OnInit } from '@angular/core';
import { BrowseService, TreeChild } from '../../services/browse';

const ICON_MAP: Record<string, string> = {
  repository: 'bi-archive',
  module: 'bi-box',
  package: 'bi-folder',
  class: 'bi-file-earmark-code',
  interface: 'bi-file-earmark-ruled',
  enum: 'bi-list-ul',
  record: 'bi-file-earmark-text',
  method: 'bi-gear',
  category: 'bi-collection',
  'rest-interface': 'bi-globe',
  'rest-endpoint': 'bi-link-45deg',
  'feign-client': 'bi-cloud-arrow-up',
  'feign-endpoint': 'bi-cloud',
  'jms-destination': 'bi-mailbox',
  'jms-listener': 'bi-inbox',
  'jms-producer': 'bi-send',
  'scheduled-task': 'bi-clock',
  'http-client': 'bi-arrow-left-right',
  microservice: 'bi-hdd-stack',
};

interface TreeRow {
  id: string;
  labels: string[];
  name: string;
  depth: number;
  hasChildren: boolean;
  kind: string;
  expanded: boolean;
  loading: boolean;
}

@Component({
  selector: 'app-node-tree',
  standalone: true,
  imports: [],
  templateUrl: './node-tree.html',
  styleUrl: './node-tree.scss',
})
export class NodeTree implements OnInit {
  perspective = input<string>('technical');
  nodeSelected = output<TreeChild>();

  rows = signal<TreeRow[]>([]);
  rootLoading = signal(true);

  private browseService = inject(BrowseService);

  ngOnInit() {
    this.loadRoots();
  }

  private loadRoots() {
    this.rootLoading.set(true);
    this.browseService.getTreeChildren(this.perspective()).subscribe({
      next: children => {
        this.rows.set(children.map(c => ({
          id: c.id,
          labels: c.labels,
          name: c.name,
          depth: 0,
          hasChildren: c.has_children,
          kind: c.kind,
          expanded: false,
          loading: false,
        })));
        this.rootLoading.set(false);
      },
      error: () => this.rootLoading.set(false),
    });
  }

  toggleExpand(index: number) {
    const current = this.rows();
    const row = current[index];
    if (!row.hasChildren) return;

    if (row.expanded) {
      this.collapse(index);
    } else {
      this.expand(index);
    }
  }

  private expand(index: number) {
    const current = [...this.rows()];
    const row = { ...current[index], expanded: true, loading: true };
    current[index] = row;
    this.rows.set(current);

    this.browseService.getTreeChildren(this.perspective(), row.id).subscribe({
      next: children => {
        const updated = [...this.rows()];
        updated[index] = { ...updated[index], loading: false };

        const childRows: TreeRow[] = children.map(c => ({
          id: c.id,
          labels: c.labels,
          name: c.name,
          depth: row.depth + 1,
          hasChildren: c.has_children,
          kind: c.kind,
          expanded: false,
          loading: false,
        }));

        updated.splice(index + 1, 0, ...childRows);
        this.rows.set(updated);
      },
      error: () => {
        const updated = [...this.rows()];
        updated[index] = { ...updated[index], loading: false, expanded: false };
        this.rows.set(updated);
      },
    });
  }

  private collapse(index: number) {
    const current = [...this.rows()];
    const parentDepth = current[index].depth;
    current[index] = { ...current[index], expanded: false };

    // Remove all descendants (rows with depth > parentDepth until we hit same or lower)
    let removeCount = 0;
    for (let i = index + 1; i < current.length; i++) {
      if (current[i].depth > parentDepth) {
        removeCount++;
      } else {
        break;
      }
    }
    current.splice(index + 1, removeCount);
    this.rows.set(current);
  }

  selectNode(row: TreeRow) {
    this.nodeSelected.emit({
      id: row.id,
      labels: row.labels,
      name: row.name,
      has_children: row.hasChildren,
      kind: row.kind,
    });
  }

  getIcon(kind: string): string {
    return ICON_MAP[kind] || 'bi-circle';
  }
}
