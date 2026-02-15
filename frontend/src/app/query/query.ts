import {
  Component, inject, signal, computed, effect, OnDestroy, ElementRef, ViewChild, AfterViewInit,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { QueryService } from '../services/query';
import { GraphRenderer } from './graph-renderer';
import hljs from 'highlight.js/lib/core';
import java from 'highlight.js/lib/languages/java';

hljs.registerLanguage('java', java);

@Component({
  selector: 'app-query',
  imports: [FormsModule],
  templateUrl: './query.html',
  styleUrl: './query.scss',
})
export class QueryComponent implements AfterViewInit, OnDestroy {
  private queryService = inject(QueryService);

  @ViewChild('graphContainer', { static: false }) graphContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('sourceBody') sourceBody?: ElementRef<HTMLDivElement>;

  question = signal('');
  loading = signal(false);
  generatedCypher = signal('');
  errorMessage = signal('');
  infoMessage = signal('');
  nodeCount = signal(0);
  edgeCount = signal(0);

  togglePub = signal(true);
  togglePriv = signal(true);
  toggleCtor = signal(true);
  toggleArgs = signal(false);

  sourceVisible = signal(false);
  sourceClassId = signal('');
  sourceClassName = signal('');
  sourceCode = signal('');
  sourceMethods = signal<{ id: string; name: string; startLine: number; endLine: number }[]>([]);
  highlightedMethodId = signal('');

  highlightedLines = computed(() => {
    const code = this.sourceCode();
    if (!code) return [];
    const highlighted = hljs.highlight(code, { language: 'java' }).value;
    return splitHighlightedLines(highlighted);
  });

  isLineHighlighted(lineIdx: number): boolean {
    const methodId = this.highlightedMethodId();
    if (!methodId) return false;
    const method = this.sourceMethods().find(m => m.id === methodId);
    if (!method || method.startLine < 0) return false;
    return lineIdx >= method.startLine && lineIdx <= method.endLine;
  }

  private renderer: GraphRenderer | null = null;

  constructor() {
    effect(() => {
      const methodId = this.highlightedMethodId();
      if (!methodId || !this.sourceBody) return;
      const method = this.sourceMethods().find(m => m.id === methodId);
      if (!method || method.startLine < 0) return;
      // Defer until after Angular renders the new source lines
      setTimeout(() => {
        const el = this.sourceBody?.nativeElement.querySelector(
          `.source-line[data-line="${method.startLine + 1}"]`
        ) as HTMLElement | null;
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    });
  }

  contextMenuVisible = signal(false);
  contextMenuX = signal(0);
  contextMenuY = signal(0);
  contextMenuItems = signal<{ label: string; icon: string; action: string; x: number; y: number; color: string }[]>([]);
  private contextMenuNodeId: string | null = null;

  ngAfterViewInit() {
    this.renderer = new GraphRenderer(this.graphContainer.nativeElement);
    this.renderer.setGlobalToggles({
      pub: this.togglePub(), priv: this.togglePriv(),
      ctor: this.toggleCtor(), args: this.toggleArgs(),
    });
    this.renderer.onNodeRightClick = (nodeId, labels, screenX, screenY) => {
      const isClass = labels.includes('Class');
      const isMethod = labels.includes('Method');
      if (!isClass && !isMethod) return;

      // Right-click also selects the node
      if (isClass) {
        this.selectClass(nodeId);
      } else {
        const classId = this.renderer?.getMethodClassId(nodeId);
        if (classId) {
          this.selectClass(classId);
          this.highlightedMethodId.set(nodeId);
          this.renderer?.setSelection(classId, nodeId);
        }
      }

      // Show radial menu
      this.contextMenuNodeId = nodeId;
      const rect = this.graphContainer.nativeElement.getBoundingClientRect();
      this.contextMenuX.set(screenX - rect.left);
      this.contextMenuY.set(screenY - rect.top);
      this.contextMenuItems.set(this.buildRadialItems(isClass ? 'Class' : 'Method'));
      this.contextMenuVisible.set(true);
    };
    this.renderer.onClassClick = (classId) => {
      this.selectClass(classId);
    };
    this.renderer.onMethodClick = (classId, methodId) => {
      this.selectClass(classId);
      this.highlightedMethodId.set(methodId);
      this.renderer?.setSelection(classId, methodId);
    };
  }

  ngOnDestroy() {
    this.renderer?.destroy();
  }

  submitQuery() {
    const q = this.question().trim();
    if (!q || this.loading()) return;

    this.loading.set(true);
    this.errorMessage.set('');
    this.infoMessage.set('');
    this.contextMenuVisible.set(false);

    this.queryService.executeQuery(q).subscribe({
      next: (result) => {
        this.loading.set(false);
        this.generatedCypher.set(result.cypher);
        if (result.error) {
          this.errorMessage.set(result.error);
        } else {
          this.renderer?.setData(result.nodes, result.relationships);
          this.updateCounts();
          this.autoSelectFirstClass();
        }
      },
      error: (err) => {
        this.loading.set(false);
        this.errorMessage.set(err.error?.detail || 'Query failed');
      },
    });
  }

  private buildRadialItems(type: 'Class' | 'Method') {
    let isFocused = false;
    if (type === 'Method' && this.contextMenuNodeId) {
      const classId = this.renderer?.getMethodClassId(this.contextMenuNodeId);
      if (classId) isFocused = this.renderer?.hasMethodFocus(classId) || false;
    }

    const defs = type === 'Class' ? [
      { label: 'Downstream', icon: 'bi-arrow-down-right', action: 'class_downstream', color: '#339af0' },
      { label: 'Upstream', icon: 'bi-arrow-up-left', action: 'class_upstream', color: '#845ef7' },
      { label: 'Imports', icon: 'bi-box-arrow-up-right', action: 'show_imports', color: '#22b8cf' },
      { label: 'Imported by', icon: 'bi-box-arrow-in-down-left', action: 'show_imported_by', color: '#20c997' },
      { label: 'Focus', icon: 'bi-fullscreen', action: 'focus', color: '#fcc419' },
      { label: 'Delete', icon: 'bi-trash', action: 'delete', color: '#ff6b6b' },
    ] : [
      { label: 'Downstream', icon: 'bi-arrow-down-right', action: 'downstream_calls', color: '#339af0' },
      { label: 'Upstream', icon: 'bi-arrow-up-left', action: 'upstream_calls', color: '#845ef7' },
      isFocused
        ? { label: 'Show All', icon: 'bi-arrows-expand', action: 'restore_methods', color: '#fcc419' }
        : { label: 'Focus', icon: 'bi-bullseye', action: 'focus_method', color: '#fcc419' },
      { label: 'Delete', icon: 'bi-trash', action: 'delete', color: '#ff6b6b' },
    ];
    const R = 64;
    const N = defs.length;
    return defs.map((d, i) => {
      const angle = -Math.PI / 2 + i * (2 * Math.PI / N);
      return { ...d, x: R * Math.cos(angle), y: R * Math.sin(angle) };
    });
  }

  executeMenuAction(action: string) {
    this.contextMenuVisible.set(false);
    if (!this.contextMenuNodeId) return;

    if (action === 'delete') {
      const nodeId = this.contextMenuNodeId;
      if (this.sourceClassId() === nodeId) this.closeSource();
      if (this.highlightedMethodId() === nodeId) this.highlightedMethodId.set('');
      this.renderer?.deleteNode(nodeId);
      this.updateCounts();
      return;
    }
    if (action === 'focus') {
      this.renderer?.focusOnClass(this.contextMenuNodeId);
      this.updateCounts();
      return;
    }
    if (action === 'focus_method') {
      const classId = this.renderer?.getMethodClassId(this.contextMenuNodeId);
      if (classId) this.renderer?.focusMethod(classId, this.contextMenuNodeId);
      return;
    }
    if (action === 'restore_methods') {
      const classId = this.renderer?.getMethodClassId(this.contextMenuNodeId);
      if (classId) this.renderer?.restoreMethods(classId);
      return;
    }
    // Expand operations
    this.loading.set(true);
    this.infoMessage.set('');
    this.errorMessage.set('');
    const countBefore = (this.renderer?.getNodeCount() || 0) + (this.renderer?.getEdgeCount() || 0);

    this.queryService.expandNode(this.contextMenuNodeId, action).subscribe({
      next: (result) => {
        this.loading.set(false);
        if (result.error) {
          this.errorMessage.set(result.error);
        } else {
          this.renderer?.addData(result.nodes, result.relationships);
          this.updateCounts();
          const countAfter = (this.renderer?.getNodeCount() || 0) + (this.renderer?.getEdgeCount() || 0);
          if (countAfter === countBefore) {
            this.infoMessage.set(`No new ${action.replace(/_/g, ' ')} found`);
          }
        }
      },
      error: (err) => {
        this.loading.set(false);
        this.errorMessage.set(err.error?.detail || 'Operation failed');
      },
    });
  }

  clearGraph() {
    this.renderer?.clear();
    this.generatedCypher.set('');
    this.errorMessage.set('');
    this.infoMessage.set('');
    this.contextMenuVisible.set(false);
    this.updateCounts();
  }

  toggleFilter(key: string) {
    switch (key) {
      case 'pub': this.togglePub.update(v => !v); break;
      case 'priv': this.togglePriv.update(v => !v); break;
      case 'ctor': this.toggleCtor.update(v => !v); break;
      case 'args': this.toggleArgs.update(v => !v); break;
    }
    this.renderer?.setGlobalToggles({
      pub: this.togglePub(),
      priv: this.togglePriv(),
      ctor: this.toggleCtor(),
      args: this.toggleArgs(),
    });
  }

  selectClass(classId: string) {
    const info = this.renderer?.getSourceInfo(classId);
    if (info && info.sourceCode) {
      const wasHidden = !this.sourceVisible();
      this.sourceVisible.set(true);
      this.sourceClassId.set(classId);
      this.sourceClassName.set(info.className);
      this.sourceCode.set(info.sourceCode);
      this.sourceMethods.set(info.methods);
      this.highlightedMethodId.set('');
      this.renderer?.setSelection(classId, null);
      if (wasHidden) setTimeout(() => this.renderer?.resize(), 0);
    }
  }

  closeSource() {
    this.sourceVisible.set(false);
    this.renderer?.setSelection(null, null);
    setTimeout(() => this.renderer?.resize(), 0);
  }

  dismissContextMenu() {
    this.contextMenuVisible.set(false);
  }

  private autoSelectFirstClass() {
    if (this.sourceVisible()) return;
    const firstId = this.renderer?.getFirstClassId();
    if (firstId) this.selectClass(firstId);
  }

  private updateCounts() {
    this.nodeCount.set(this.renderer?.getNodeCount() || 0);
    this.edgeCount.set(this.renderer?.getEdgeCount() || 0);
  }
}

/** Split highlight.js HTML into per-line fragments, balancing open/close spans across lines. */
function splitHighlightedLines(html: string): string[] {
  const lines = html.split('\n');
  const result: string[] = [];
  let openSpans: string[] = [];

  for (const line of lines) {
    const prefix = openSpans.join('');

    // Track span open/close in this line to determine carry-over
    const tagRegex = /<(\/?)span([^>]*)>/g;
    const newOpen = [...openSpans];
    let match;
    while ((match = tagRegex.exec(line)) !== null) {
      if (match[1] === '/') {
        newOpen.pop();
      } else {
        newOpen.push(`<span${match[2]}>`);
      }
    }

    const suffix = '</span>'.repeat(newOpen.length);
    result.push(prefix + line + suffix);
    openSpans = newOpen;
  }

  return result;
}
