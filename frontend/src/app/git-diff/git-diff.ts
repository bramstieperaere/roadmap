import { Component, ElementRef, inject, OnInit, signal, computed, viewChild } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { parseReturnTo } from '../shared/return-to';

interface RepoDiff {
  repo_name: string;
  path: string;
  branch: string;
  diff: string;
  stat: string;
  untracked: string[];
}

@Component({
  selector: 'app-git-diff',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './git-diff.html',
  styleUrl: './git-diff.scss',
})
export class GitDiff implements OnInit {
  private route = inject(ActivatedRoute);
  private http = inject(HttpClient);

  repoName = signal('');
  returnTo = signal('');
  loading = signal(true);
  error = signal('');
  data = signal<RepoDiff | null>(null);
  activeFile = signal('');
  fileListCollapsed = signal(false);
  filterText = signal('');
  diffPanel = viewChild<ElementRef<HTMLElement>>('diffPanel');

  parsedFiles = computed(() => {
    const d = this.data();
    if (!d?.diff) return [];
    return parseDiffFiles(d.diff);
  });

  totalAdded = computed(() => this.parsedFiles().reduce((s, f) => s + f.added, 0));
  totalRemoved = computed(() => this.parsedFiles().reduce((s, f) => s + f.removed, 0));

  fileTree = computed(() => buildFileTree(this.parsedFiles(), this.data()?.untracked ?? []));

  filteredTree = computed(() => {
    const q = this.filterText().toLowerCase();
    if (!q) return this.fileTree();
    return this.fileTree()
      .map(g => ({
        ...g,
        files: g.files.filter(f => f.path.toLowerCase().includes(q)),
      }))
      .filter(g => g.files.length > 0);
  });

  backLink = computed(() => parseReturnTo(this.returnTo()));

  ngOnInit() {
    const repo = this.route.snapshot.paramMap.get('repo') ?? '';
    this.repoName.set(repo);
    this.returnTo.set(this.route.snapshot.queryParamMap.get('returnTo') ?? '');
    this.loadDiff();
  }

  loadDiff() {
    this.loading.set(true);
    this.error.set('');
    this.http.get<RepoDiff>(`/api/contexts/meta/repo-diff/${encodeURIComponent(this.repoName())}`)
      .subscribe({
        next: d => { this.data.set(d); this.loading.set(false); },
        error: e => { this.error.set(e.error?.detail ?? 'Failed to load diff'); this.loading.set(false); },
      });
  }

  browseDirParams() {
    const returnTo = this.returnTo();
    let back = `/git-diff/${encodeURIComponent(this.repoName())}`;
    if (returnTo) back += `?returnTo=${encodeURIComponent(returnTo)}`;
    return { path: this.data()?.path ?? '', returnTo: back };
  }

  scrollToFile(path: string) {
    this.activeFile.set(path);
    const panel = this.diffPanel()?.nativeElement;
    const el = document.getElementById(this.fileAnchor(path));
    if (panel && el) {
      const panelRect = panel.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      panel.scrollTop += elRect.top - panelRect.top;
    }
  }

  fileAnchor(path: string) {
    return 'diff-' + path;
  }
}

// ── Diff parser ──

interface DiffFile {
  path: string;
  hunks: DiffHunk[];
  added: number;
  removed: number;
}

interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

interface DiffLine {
  type: 'add' | 'remove' | 'context';
  content: string;
  oldLine: number | null;
  newLine: number | null;
}

interface FileTreeGroup {
  dir: string;
  collapsed: boolean;
  files: FileTreeEntry[];
}

interface FileTreeEntry {
  path: string;
  name: string;
  added: number;
  removed: number;
  untracked: boolean;
}

function parseDiffFiles(raw: string): DiffFile[] {
  const files: DiffFile[] = [];
  const fileParts = raw.split(/^diff --git /m);

  for (const part of fileParts) {
    if (!part.trim()) continue;

    const firstLine = part.split('\n')[0];
    const match = firstLine.match(/a\/(.+?) b\/(.+)/);
    const path = match ? match[2] : firstLine;

    const hunks: DiffHunk[] = [];
    let added = 0;
    let removed = 0;
    const hunkParts = part.split(/^(@@\s*-(\d+)(?:,\d+)?\s*\+(\d+)(?:,\d+)?\s*@@.*$)/m);

    // hunkParts: [preamble, fullHeader, oldStart, newStart, body, ...]
    for (let i = 1; i < hunkParts.length; i += 4) {
      const header = hunkParts[i];
      const oldStart = parseInt(hunkParts[i + 1], 10);
      const newStart = parseInt(hunkParts[i + 2], 10);
      const body = hunkParts[i + 3] ?? '';
      const lines: DiffLine[] = [];
      let oldLine = oldStart;
      let newLine = newStart;

      for (const line of body.split('\n')) {
        if (line.startsWith('+')) {
          lines.push({ type: 'add', content: line, oldLine: null, newLine });
          newLine++;
          added++;
        } else if (line.startsWith('-')) {
          lines.push({ type: 'remove', content: line, oldLine, newLine: null });
          oldLine++;
          removed++;
        } else if (line.startsWith('\\')) {
          // "\ No newline at end of file"
        } else if (line !== '' || lines.length > 0) {
          lines.push({ type: 'context', content: line || ' ', oldLine, newLine });
          oldLine++;
          newLine++;
        }
      }

      // Trim trailing empty context lines
      while (lines.length && lines[lines.length - 1].type === 'context'
             && lines[lines.length - 1].content.trim() === '') {
        lines.pop();
      }

      hunks.push({ header, lines });
    }

    files.push({ path, hunks, added, removed });
  }
  return files;
}

function buildFileTree(files: DiffFile[], untracked: string[]): FileTreeGroup[] {
  const untrackedSet = new Set(untracked);
  const entries: FileTreeEntry[] = [];

  for (const f of files) {
    const slash = f.path.lastIndexOf('/');
    entries.push({
      path: f.path,
      name: slash >= 0 ? f.path.substring(slash + 1) : f.path,
      added: f.added,
      removed: f.removed,
      untracked: untrackedSet.has(f.path),
    });
  }

  // Group by directory
  const groups = new Map<string, FileTreeEntry[]>();
  for (const e of entries) {
    const slash = e.path.lastIndexOf('/');
    const dir = slash >= 0 ? e.path.substring(0, slash) : '';
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(e);
  }

  return Array.from(groups.entries()).map(([dir, files]) => ({
    dir,
    collapsed: false,
    files,
  }));
}
