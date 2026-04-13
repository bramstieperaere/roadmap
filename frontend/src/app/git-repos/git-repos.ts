import { Component, computed, HostListener, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Location } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GitMiningService, BranchInfo, CommitInfo, ProcessorInfo } from '../services/git-mining';
import { JiraService, JiraIssue } from '../services/jira';
import { SettingsService, RepositoryConfig } from '../services/settings';

@Component({
  selector: 'app-git-repos',
  standalone: true,
  imports: [RouterLink, FormsModule],
  templateUrl: './git-repos.html',
  styleUrl: './git-repos.scss',
})
export class GitRepos implements OnInit {
  private route = inject(ActivatedRoute);
  private location = inject(Location);
  private gitService = inject(GitMiningService);
  private settingsService = inject(SettingsService);

  // Data
  repos = signal<RepositoryConfig[]>([]);
  branches = signal<BranchInfo[]>([]);
  commits = signal<CommitInfo[]>([]);
  loadingCommits = signal(false);
  loadingBranches = signal(false);
  error = signal('');

  // Processor filters
  processors = signal<ProcessorInfo[]>([]);
  activeProcessorFilters = signal<Set<string>>(new Set());
  filterPopupOpen = signal(false);
  detailFilterPopupOpen = signal(false);
  detailFilterActive = signal(false);

  filteredCommits = computed(() => {
    const all = this.commits();
    const filters = this.activeProcessorFilters();
    if (filters.size === 0) return all;
    const props = this.processors()
      .filter(p => filters.has(p.name))
      .map(p => p.node_property);
    return all.filter(c => props.some(prop => this.hasProcessorChanges(c, prop)));
  });

  // Repo picker
  repoQuery = signal('');
  repoOpen = signal(false);
  selectedRepo = signal('');

  filteredRepos = computed(() => {
    const q = this.repoQuery().toLowerCase().trim();
    const all = this.repos();
    if (!q) return all;
    return all.filter(r => r.name.toLowerCase().includes(q));
  });

  // Branch picker
  branchQuery = signal('');
  branchOpen = signal(false);
  selectedBranch = signal('');

  filteredBranches = computed(() => {
    const q = this.branchQuery().toLowerCase().trim();
    const all = this.branches();
    if (!q) return all;
    return all.filter(b => b.name.toLowerCase().includes(q));
  });

  ngOnInit() {
    this.settingsService.getSettings().subscribe({
      next: (cfg) => {
        this.repos.set(cfg.repositories);
        // Restore from route params (deep-link support)
        const params = this.route.snapshot.paramMap;
        const repo = params.get('repo');
        const branch = params.get('branch');
        if (repo) {
          this.selectRepo(repo);
          if (branch) {
            // selectRepo loads branches; once loaded, auto-select branch
            const sub = this.gitService.getNeo4jBranches(repo).subscribe({
              next: (b) => {
                this.branches.set(b);
                this.loadingBranches.set(false);
                if (b.some(br => br.name === branch)) {
                  this.selectedBranch.set(branch);
                  this.branchQuery.set(branch);
                  this.loadCommits(repo, branch);
                }
              },
            });
          }
        }
      },
    });
    this.gitService.getProcessors().subscribe({
      next: (p) => this.processors.set(p),
    });
  }

  isDetailCardVisible(processorName: string): boolean {
    if (!this.detailFilterActive()) return true;
    if (this.activeProcessorFilters().size === 0) return true;
    // Map processor names to their node_property for matching
    const proc = this.processors().find(p => p.name === processorName);
    return proc ? this.activeProcessorFilters().has(proc.name) : true;
  }

  isDetailCardVisibleByProp(nodeProperty: string): boolean {
    if (!this.detailFilterActive()) return true;
    if (this.activeProcessorFilters().size === 0) return true;
    const proc = this.processors().find(p => p.node_property === nodeProperty);
    return proc ? this.activeProcessorFilters().has(proc.name) : true;
  }

  getProcessorLabel(name: string): string {
    return this.processors().find(p => p.name === name)?.label || name;
  }

  toggleProcessorFilter(name: string) {
    this.activeProcessorFilters.update(s => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
    // Close popup if removing from badge click (not from popup)
  }

  // ── Repo selection ──

  onRepoInput(value: string) {
    this.repoQuery.set(value);
    this.repoOpen.set(true);
    // If typed value doesn't match current selection, clear downstream
    if (value !== this.selectedRepo()) {
      this.selectedRepo.set('');
      this.branches.set([]);
      this.selectedBranch.set('');
      this.branchQuery.set('');
      this.commits.set([]);
    }
  }

  selectRepo(name: string) {
    this.selectedRepo.set(name);
    this.repoQuery.set(name);
    this.repoOpen.set(false);
    this.selectedBranch.set('');
    this.branchQuery.set('');
    this.commits.set([]);
    this.loadBranches(name);
  }

  clearRepo() {
    this.repoQuery.set('');
    this.selectedRepo.set('');
    this.branches.set([]);
    this.noBranches.set(false);
    this.branchQuery.set('');
    this.selectedBranch.set('');
    this.commits.set([]);
    this.selectedCommit.set(null);
    this.mergeSourceCommits.set([]);
  }

  onRepoFocus() { this.repoOpen.set(true); }
  onRepoBlur() { setTimeout(() => this.repoOpen.set(false), 200); }

  noBranches = signal(false);

  private loadBranches(repoName: string) {
    this.loadingBranches.set(true);
    this.noBranches.set(false);
    this.gitService.getNeo4jBranches(repoName).subscribe({
      next: (b) => { this.branches.set(b); this.noBranches.set(b.length === 0); this.loadingBranches.set(false); },
      error: () => { this.branches.set([]); this.noBranches.set(true); this.loadingBranches.set(false); },
    });
  }

  // ── Branch selection ──

  onBranchInput(value: string) {
    this.branchQuery.set(value);
    this.branchOpen.set(true);
    if (value !== this.selectedBranch()) {
      this.selectedBranch.set('');
      this.commits.set([]);
    }
  }

  selectBranch(name: string) {
    this.selectedBranch.set(name);
    this.branchQuery.set(name);
    this.branchOpen.set(false);
    this.loadCommits(this.selectedRepo(), name);
    this.location.replaceState(`/git-repos/${encodeURIComponent(this.selectedRepo())}/${encodeURIComponent(name)}`);
  }

  clearBranch() {
    this.branchQuery.set('');
    this.selectedBranch.set('');
    this.commits.set([]);
    this.selectedCommit.set(null);
    this.mergeSourceCommits.set([]);
  }

  onBranchFocus() { this.branchOpen.set(true); }
  onBranchBlur() { setTimeout(() => this.branchOpen.set(false), 200); }

  private loadCommits(repo: string, branch: string) {
    this.loadingCommits.set(true);
    this.error.set('');
    this.gitService.getCommits(repo, branch).subscribe({
      next: (c) => { this.commits.set(c); this.loadingCommits.set(false); },
      error: (err) => { this.error.set(err.error?.detail || 'Failed to load commits'); this.loadingCommits.set(false); },
    });
  }

  // ── Commit detail / Jira ──
  private jiraService = inject(JiraService);

  selectedCommit = signal<CommitInfo | null>(null);
  jiraIssues = signal<Map<string, JiraIssue>>(new Map());
  loadingIssues = signal(false);
  mergeSourceCommits = signal<CommitInfo[]>([]);
  loadingMergeSource = signal(false);

  // Source viewer (third column)
  sourceFile = signal<{ path: string; content: string; addedLines: Set<number>; removedLines: Set<number> } | null>(null);
  loadingSource = signal(false);

  // Merge rollup
  mergeRollup = signal<Record<string, any> | null>(null);
  loadingRollup = signal(false);

  @HostListener('window:keydown', ['$event'])
  onKeydown(event: KeyboardEvent) {
    if (!this.selectedCommit()) return;
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    // Don't intercept if user is typing in an input
    const tag = (event.target as HTMLElement)?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    event.preventDefault();
    const commits = this.filteredCommits();
    const idx = commits.findIndex(c => c.hash === this.selectedCommit()?.hash);
    if (idx < 0) return;
    const next = event.key === 'ArrowDown' ? idx + 1 : idx - 1;
    if (next >= 0 && next < commits.length) {
      this.selectCommit(commits[next]);
      // Scroll into view
      setTimeout(() => {
        document.querySelector('.timeline-card.active')
          ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
    }
  }

  selectCommit(commit: CommitInfo) {
    if (this.selectedCommit()?.hash === commit.hash) {
      this.selectedCommit.set(null);
      this.mergeSourceCommits.set([]);
      this.mergeRollup.set(null);
      this.sourceFile.set(null);
      return;
    }
    this.selectedCommit.set(commit);
    this.mergeSourceCommits.set([]);
    this.mergeRollup.set(null);
    this.sourceFile.set(null);
    if (commit.issue_keys.length > 0) {
      this.loadIssues(commit.issue_keys);
    }
    if (this.isMergeCommit(commit)) {
      this.loadMergeSource(commit.hash);
      this.loadMergeRollup(commit.hash);
    }
  }

  private loadMergeRollup(hash: string) {
    this.loadingRollup.set(true);
    this.gitService.getMergeRollup(hash, this.selectedRepo()).subscribe({
      next: (data) => { this.mergeRollup.set(data); this.loadingRollup.set(false); },
      error: () => { this.mergeRollup.set(null); this.loadingRollup.set(false); },
    });
  }

  isMergeCommit(commit: CommitInfo): boolean {
    const msg = commit.message?.toLowerCase() || '';
    return msg.startsWith('merge') || msg.startsWith('merged in');
  }

  private loadMergeSource(hash: string) {
    this.loadingMergeSource.set(true);
    this.gitService.getMergeSourceCommits(hash, this.selectedRepo(), this.selectedBranch()).subscribe({
      next: (commits) => { this.mergeSourceCommits.set(commits); this.loadingMergeSource.set(false); },
      error: () => { this.mergeSourceCommits.set([]); this.loadingMergeSource.set(false); },
    });
  }

  private loadIssues(keys: string[]) {
    const cached = this.jiraIssues();
    const missing = keys.filter(k => !cached.has(k));
    if (missing.length === 0) return;
    this.loadingIssues.set(true);
    let loaded = 0;
    for (const key of missing) {
      this.jiraService.getIssue(key).subscribe({
        next: (issue) => {
          this.jiraIssues.update(m => { const n = new Map(m); n.set(key, issue); return n; });
          if (++loaded >= missing.length) this.loadingIssues.set(false);
        },
        error: () => { if (++loaded >= missing.length) this.loadingIssues.set(false); },
      });
    }
  }

  getIssue(key: string): JiraIssue | undefined {
    return this.jiraIssues().get(key);
  }

  statusClass(status: string): string {
    const s = status?.toLowerCase() || '';
    if (s === 'done' || s === 'closed' || s === 'resolved') return 'status-done';
    if (s === 'in progress' || s === 'in review') return 'status-progress';
    return 'status-todo';
  }

  viewSource(filePath: string) {
    const commit = this.selectedCommit();
    const repo = this.selectedRepo();
    if (!commit || !repo || !filePath) return;
    this.loadingSource.set(true);
    this.gitService.getFileAtCommit(repo, commit.hash, filePath).subscribe({
      next: (res) => {
        this.sourceFile.set({
          path: res.path,
          content: res.content,
          addedLines: new Set(res.added_lines || []),
          removedLines: new Set(res.removed_lines || []),
        });
        this.loadingSource.set(false);
      },
      error: () => { this.sourceFile.set(null); this.loadingSource.set(false); },
    });
  }

  sourceLines(): { num: number; text: string; type: 'added' | 'removed' | '' }[] {
    const file = this.sourceFile();
    if (!file) return [];
    return file.content.split('\n').map((text, i) => {
      const num = i + 1;
      let type: 'added' | 'removed' | '' = '';
      if (file.addedLines.has(num)) type = 'added';
      else if (file.removedLines.has(num)) type = 'removed';
      return { num, text, type };
    });
  }

  sourceFileName(): string {
    const path = this.sourceFile()?.path || '';
    return path.split('/').pop() || path;
  }

  hasProcessorChanges(commit: CommitInfo, field: string): boolean {
    const data = this.parseProcessorData(commit, field);
    if (!data) return false;
    if (!data.diff) return true; // no diff = initial commit, show it
    const d = data.diff;
    return !!(d.added_entities?.length || d.modified_entities?.length || d.removed_entities?.length
      || d.added_endpoints?.length || d.removed_endpoints?.length);
  }

  findEntityFile(entities: any[], className: string): string {
    const e = entities?.find((e: any) => e.class === className);
    return e?.file || '';
  }

  parseProcessorData(commit: CommitInfo, field: string): any | null {
    const raw = (commit as any)[field];
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return null; }
  }

  parseDbChanges(commit: CommitInfo): { files: string[]; changes: any[] } | null {
    if (!commit.db_changes) return null;
    try {
      return JSON.parse(commit.db_changes);
    } catch {
      return null;
    }
  }

  dbChangeIcon(op: string): string {
    switch (op) {
      case 'createTable': return 'bi-plus-circle text-success';
      case 'dropTable': return 'bi-dash-circle text-danger';
      case 'renameTable': return 'bi-arrow-left-right text-info';
      case 'addColumn': return 'bi-plus text-success';
      case 'dropColumn': return 'bi-dash text-danger';
      case 'renameColumn': return 'bi-arrow-left-right text-info';
      case 'modifyDataType': return 'bi-pencil text-warning';
      case 'addPrimaryKey': return 'bi-key text-primary';
      case 'addForeignKeyConstraint': return 'bi-link text-primary';
      case 'createIndex': return 'bi-lightning text-warning';
      case 'dropIndex': return 'bi-lightning text-danger';
      case 'addUniqueConstraint': return 'bi-shield-check text-primary';
      case 'addNotNullConstraint': return 'bi-exclamation-circle text-warning';
      case 'sql': return 'bi-code-square text-secondary';
      case 'createSequence': return 'bi-plus-circle text-info';
      case 'dropSequence': return 'bi-dash-circle text-danger';
      default: return 'bi-dot text-muted';
    }
  }

  dbChangeLabel(change: any): string {
    const op = change.op;
    switch (op) {
      case 'createTable': {
        const cols = (change.columns || []).map((c: any) => c.name).join(', ');
        return `CREATE TABLE ${change.table} (${cols})`;
      }
      case 'dropTable': return `DROP TABLE ${change.table}`;
      case 'renameTable': return `RENAME TABLE ${change.oldTable} → ${change.newTable}`;
      case 'addColumn': {
        const cols = (change.columns || []).map((c: any) => `${c.name} ${c.type || ''}`).join(', ');
        return `ADD COLUMN ${change.table} (${cols})`;
      }
      case 'dropColumn': return `DROP COLUMN ${change.table}.${change.column}`;
      case 'renameColumn': return `RENAME COLUMN ${change.table}.${change.oldName} → ${change.newName}`;
      case 'modifyDataType': return `MODIFY ${change.table}.${change.column} → ${change.newType}`;
      case 'addPrimaryKey': return `ADD PK ${change.table} (${change.columns})`;
      case 'addForeignKeyConstraint': return `ADD FK ${change.baseTable}.${change.baseColumn} → ${change.refTable}.${change.refColumn}`;
      case 'createIndex': return `CREATE INDEX ${change.index || ''} ON ${change.table}`;
      case 'dropIndex': return `DROP INDEX ${change.index}`;
      case 'addUniqueConstraint': return `ADD UNIQUE ${change.table} (${change.columns})`;
      case 'addNotNullConstraint': return `ADD NOT NULL ${change.table}.${change.column}`;
      case 'dropNotNullConstraint': return `DROP NOT NULL ${change.table}.${change.column}`;
      case 'sql': return `SQL: ${(change.sql || '').substring(0, 80)}`;
      case 'createSequence': return `CREATE SEQUENCE ${change.sequence}`;
      case 'dropSequence': return `DROP SEQUENCE ${change.sequence}`;
      default: return op;
    }
  }

  coveragePercent(commit: CommitInfo): number | null {
    if (!commit.documented_files?.length || !commit.files_count) return null;
    return Math.round((commit.documented_files.length / commit.files_count) * 100);
  }

  // ── Helpers ──

  formatDate(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  formatTime(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  }

  firstLine(msg: string): string {
    return msg?.split('\n')[0] || '';
  }

  commitsByDate(): { date: string; commits: CommitInfo[] }[] {
    const groups: Map<string, CommitInfo[]> = new Map();
    for (const c of this.filteredCommits()) {
      const day = this.formatDate(c.date);
      const arr = groups.get(day);
      if (arr) arr.push(c);
      else groups.set(day, [c]);
    }
    return [...groups.entries()].map(([date, commits]) => ({ date, commits }));
  }
}
