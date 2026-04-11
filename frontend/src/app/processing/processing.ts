import { Component, inject, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { SettingsService, RepositoryConfig } from '../services/settings';
import { JobsService } from '../services/jobs';
import { GitMiningService } from '../services/git-mining';

type ActionType = 'analysis' | 'enrichment' | 'data-flow' | 'ingest-commits' | 'link-jira-tickets';
type SourceType = 'git-repo';

interface ActionDefinition {
  type: ActionType;
  title: string;
  description: string;
  icon: string;
  iconBg: string;
  iconColor: string;
}

interface ActiveJob {
  action: ActionDefinition;
}

@Component({
  selector: 'app-processing',
  imports: [RouterLink],
  templateUrl: './processing.html',
  styleUrl: './processing.scss',
})
export class Processing implements OnInit {
  private settingsService = inject(SettingsService);
  private jobsService = inject(JobsService);
  private gitMiningService = inject(GitMiningService);
  private router = inject(Router);

  repos = signal<RepositoryConfig[]>([]);
  activeSource = signal<SourceType>('git-repo');
  activeJob = signal<ActiveJob | null>(null);
  selected = signal<Set<number>>(new Set());
  starting = signal(false);
  message = signal<{ text: string; type: 'success' | 'danger' } | null>(null);

  // Traversal options (step 2 in modal)
  traversalMode = signal<'all' | 'branches'>('all');
  branches = signal<string[]>([]);
  selectedBranches = signal<Set<string>>(new Set());
  loadingBranches = signal(false);

  readonly actions: ActionDefinition[] = [
    { type: 'analysis', title: 'Code Analysis', description: 'Parse source code and build the class and method metamodel in Neo4j.', icon: 'bi-diagram-3', iconBg: '#e7f5ff', iconColor: '#1971c2' },
    { type: 'enrichment', title: 'Architecture Enrichment', description: 'Detect technologies and enrich the graph with architecture patterns.', icon: 'bi-layers', iconBg: '#f3f0ff', iconColor: '#6741d9' },
    { type: 'data-flow', title: 'Data Flow', description: 'Map service endpoints, queues, and database dependencies.', icon: 'bi-arrow-left-right', iconBg: '#e6fcf5', iconColor: '#0ca678' },
    { type: 'ingest-commits', title: 'Ingest Git Commits', description: 'Import commit history, branches, and file change data into Neo4j.', icon: 'bi-download', iconBg: '#fff9db', iconColor: '#f08c00' },
    { type: 'link-jira-tickets', title: 'Link Jira Tickets', description: 'Create JiraTicket stub nodes from commit messages and link them to commits.', icon: 'bi-ticket-perforated', iconBg: '#fff9db', iconColor: '#f08c00' },
  ];

  /** Whether the current action supports branch filtering. */
  get supportsTraversal(): boolean {
    const type = this.activeJob()?.action.type;
    return type === 'ingest-commits' || type === 'link-jira-tickets';
  }

  ngOnInit() {
    this.settingsService.getSettings().subscribe({
      next: (config) => this.repos.set(config.repositories),
    });
  }

  switchSource(source: SourceType) { this.activeSource.set(source); }

  openAction(type: ActionType) {
    const action = this.actions.find(a => a.type === type);
    if (!action) return;
    this.selected.set(new Set());
    this.traversalMode.set('all');
    this.branches.set([]);
    this.selectedBranches.set(new Set());
    this.activeJob.set({ action });
  }

  closeModal() { this.activeJob.set(null); }

  isSelected(i: number): boolean { return this.selected().has(i); }

  toggle(i: number) {
    this.selected.update(s => {
      const n = new Set(s);
      n.has(i) ? n.delete(i) : n.add(i);
      return n;
    });
    // If branch filtering is active and exactly one repo is selected, load branches
    if (this.traversalMode() === 'branches') {
      this.loadBranchesForSelection();
    }
  }

  selectAll() { this.selected.set(new Set(this.repos().map((_, i) => i))); }
  selectNone() { this.selected.set(new Set()); }

  get hasSelection(): boolean { return this.selected().size > 0; }

  // ── Traversal options ──

  setTraversalMode(mode: 'all' | 'branches') {
    this.traversalMode.set(mode);
    if (mode === 'branches') {
      this.loadBranchesForSelection();
    } else {
      this.branches.set([]);
      this.selectedBranches.set(new Set());
    }
  }

  private loadBranchesForSelection() {
    const indices = [...this.selected()];
    if (indices.length !== 1) {
      this.branches.set([]);
      return;
    }
    const repo = this.repos()[indices[0]];
    if (!repo) return;
    this.loadingBranches.set(true);
    this.gitMiningService.getBranches(repo.name).subscribe({
      next: (b) => { this.branches.set(b); this.loadingBranches.set(false); },
      error: () => { this.branches.set([]); this.loadingBranches.set(false); },
    });
  }

  toggleBranch(name: string) {
    this.selectedBranches.update(s => {
      const n = new Set(s);
      n.has(name) ? n.delete(name) : n.add(name);
      return n;
    });
  }

  // ── Start job ──

  confirmStart() {
    const job = this.activeJob();
    if (!job || !this.hasSelection) return;

    this.starting.set(true);
    const type = job.action.type;

    if (type === 'ingest-commits' || type === 'link-jira-tickets') {
      const repoNames = [...this.selected()].sort((a, b) => a - b)
        .map(i => this.repos()[i].name);
      const action = type === 'ingest-commits' ? 'ingest_commits' : 'link_jira_tickets';
      const branchFilter = this.traversalMode() === 'branches' ? [...this.selectedBranches()] : undefined;
      this.gitMiningService.start(action, repoNames, branchFilter).subscribe({
        next: () => { this.starting.set(false); this.activeJob.set(null); this.router.navigate(['/jobs']); },
        error: (err) => { this.starting.set(false); this.showMessage(err.error?.detail || 'Failed to start job', 'danger'); },
      });
    } else {
      const indices = [...this.selected()].sort((a, b) => a - b);
      const request = type === 'analysis'
        ? this.jobsService.startAnalysis(indices)
        : type === 'enrichment'
          ? this.jobsService.startEnrichment(indices)
          : this.jobsService.startDataFlow(indices);
      request.subscribe({
        next: () => { this.starting.set(false); this.activeJob.set(null); this.router.navigate(['/jobs']); },
        error: (err) => { this.starting.set(false); this.showMessage(err.error?.detail || 'Failed to start job', 'danger'); },
      });
    }
  }

  private showMessage(text: string, type: 'success' | 'danger') {
    this.message.set({ text, type });
    setTimeout(() => this.message.set(null), 4000);
  }
}
