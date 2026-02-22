import {
  Component, inject, signal, computed, OnDestroy, ElementRef, ViewChild, AfterViewInit,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { QueryService, EntryClass } from '../services/query';
import { SequenceRenderer, SequenceCall } from './sequence-renderer';
import hljs from 'highlight.js/lib/core';
import java from 'highlight.js/lib/languages/java';

hljs.registerLanguage('java', java);

@Component({
  selector: 'app-sequence',
  imports: [FormsModule],
  templateUrl: './sequence.html',
  styleUrl: './sequence.scss',
})
export class SequenceComponent implements AfterViewInit, OnDestroy {
  private queryService = inject(QueryService);

  @ViewChild('diagramContainer', { static: false }) diagramContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('sourceBody') sourceBody?: ElementRef<HTMLDivElement>;

  entryClasses = signal<EntryClass[]>([]);
  selectedClassId = signal('');
  selectedMethodId = signal('');
  loading = signal(false);
  errorMessage = signal('');

  sourceVisible = signal(false);
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

  selectedClassMethods = computed(() => {
    const classId = this.selectedClassId();
    const cls = this.entryClasses().find(c => c.id === classId);
    return cls?.methods || [];
  });

  isLineHighlighted(lineIdx: number): boolean {
    const methodId = this.highlightedMethodId();
    if (!methodId) return false;
    const method = this.sourceMethods().find(m => m.id === methodId);
    if (!method || method.startLine < 0) return false;
    return lineIdx >= method.startLine && lineIdx <= method.endLine;
  }

  private renderer: SequenceRenderer | null = null;

  ngAfterViewInit() {
    this.renderer = new SequenceRenderer(this.diagramContainer.nativeElement);
    this.renderer.onCallClick = (call: SequenceCall) => {
      this.selectClassAndMethod(call.targetClassId, call.targetId);
    };
    this.renderer.onHeaderClick = (classId: string) => {
      this.selectClass(classId);
    };

    // Load entry classes
    this.loading.set(true);
    this.queryService.getEntryClasses().subscribe({
      next: (classes) => {
        this.loading.set(false);
        this.entryClasses.set(classes);
      },
      error: (err) => {
        this.loading.set(false);
        this.errorMessage.set(err.error?.detail || 'Failed to load entry classes');
      },
    });
  }

  ngOnDestroy() {
    this.renderer?.destroy();
  }

  onClassChange(classId: string) {
    this.selectedClassId.set(classId);
    this.selectedMethodId.set('');
  }

  onMethodChange(methodId: string) {
    this.selectedMethodId.set(methodId);
  }

  drawSequence() {
    const methodId = this.selectedMethodId();
    if (!methodId || this.loading()) return;

    this.loading.set(true);
    this.errorMessage.set('');

    this.queryService.expandNode(methodId, 'downstream_calls', 5).subscribe({
      next: (result) => {
        this.loading.set(false);
        if (result.error) {
          this.errorMessage.set(result.error);
        } else {
          this.renderer?.setData(result.nodes, result.relationships, methodId);
          this.autoSelectSource(methodId, result.nodes, result.relationships);
        }
      },
      error: (err) => {
        this.loading.set(false);
        this.errorMessage.set(err.error?.detail || 'Failed to load sequence');
      },
    });
  }

  private autoSelectSource(
    methodId: string,
    nodes: { id: string; labels: string[]; properties: Record<string, unknown> }[],
    relationships: { type: string; start_node_id: string; end_node_id: string }[],
  ) {
    // Find the class that owns the root method
    const rel = relationships.find(r => r.type === 'HAS_METHOD' && r.end_node_id === methodId);
    if (rel) {
      this.selectClass(rel.start_node_id);
      this.highlightedMethodId.set(methodId);
    }
  }

  private selectClass(classId: string) {
    const info = this.renderer?.getSourceInfo(classId);
    if (info && info.sourceCode) {
      this.sourceVisible.set(true);
      this.sourceClassName.set(info.className);
      this.sourceCode.set(info.sourceCode);
      this.sourceMethods.set(info.methods);
      this.highlightedMethodId.set('');
    }
  }

  private selectClassAndMethod(classId: string, methodId: string) {
    this.selectClass(classId);
    this.highlightedMethodId.set(methodId);
    // Scroll to method
    setTimeout(() => {
      const method = this.sourceMethods().find(m => m.id === methodId);
      if (!method || method.startLine < 0 || !this.sourceBody) return;
      const el = this.sourceBody.nativeElement.querySelector(
        `.source-line[data-line="${method.startLine + 1}"]`
      ) as HTMLElement | null;
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  closeSource() {
    this.sourceVisible.set(false);
    setTimeout(() => this.renderer?.resize(), 0);
  }
}

/** Split highlight.js HTML into per-line fragments, balancing open/close spans across lines. */
function splitHighlightedLines(html: string): string[] {
  const lines = html.split('\n');
  const result: string[] = [];
  let openSpans: string[] = [];

  for (const line of lines) {
    const prefix = openSpans.join('');

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
