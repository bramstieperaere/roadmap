import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { GitMiningService, Repository, MiningResults, RepoResult, JiraProjectInfo } from '../services/git-mining';
import { SettingsService, VerifyProjectResult } from '../services/settings';

@Component({
  selector: 'app-git-mining',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './git-mining.html',
  styleUrl: './git-mining.scss',
})
export class GitMiningComponent implements OnInit {
  private svc = inject(GitMiningService);
  private settingsSvc = inject(SettingsService);
  private router = inject(Router);

  repos = signal<Repository[]>([]);
  selected = signal<Set<string>>(new Set());
  action = signal('find_jira_projects');
  starting = signal(false);
  loadingRepos = signal(true);

  results = signal<MiningResults | null>(null);
  loadingResults = signal(true);

  // Project import flow
  configuredKeys = signal<Set<string>>(new Set());
  projectSelection = signal<Set<string>>(new Set());
  verifying = signal(false);
  verifyResults = signal<Map<string, VerifyProjectResult>>(new Map());
  verified = signal(false);
  adding = signal(false);
  message = signal<{ type: string; text: string } | null>(null);

  selectableProjects = computed(() =>
    this.allProjects().filter(p => !this.configuredKeys().has(p.key))
  );

  ngOnInit() {
    this.svc.getRepositories().subscribe({
      next: r => { this.repos.set(r); this.loadingRepos.set(false); },
      error: () => this.loadingRepos.set(false),
    });
    this.svc.getResults().subscribe({
      next: r => { this.results.set(r); this.loadingResults.set(false); },
      error: () => this.loadingResults.set(false),
    });
    this.settingsSvc.getSettings().subscribe({
      next: cfg => {
        this.configuredKeys.set(new Set(cfg.atlassian.jira_projects.map(p => p.key)));
      },
      error: () => {},
    });
  }

  toggle(name: string) {
    const s = new Set(this.selected());
    if (s.has(name)) s.delete(name); else s.add(name);
    this.selected.set(s);
  }

  selectAll() {
    this.selected.set(new Set(this.repos().map(r => r.name)));
  }

  selectNone() {
    this.selected.set(new Set());
  }

  startMining() {
    if (this.selected().size === 0) return;
    this.starting.set(true);
    this.svc.start(this.action(), [...this.selected()]).subscribe({
      next: res => {
        this.starting.set(false);
        this.router.navigate(['/jobs', res.job_id]);
      },
      error: () => this.starting.set(false),
    });
  }

  repoResults(): RepoResult[] {
    const r = this.results();
    return r ? Object.values(r.repos) : [];
  }

  projectEntries(repo: RepoResult): { key: string; info: JiraProjectInfo }[] {
    return Object.entries(repo.projects)
      .map(([key, info]) => ({ key, info }))
      .sort((a, b) => b.info.reference_count - a.info.reference_count);
  }

  allProjects(): { key: string; uniqueIssues: number; refCount: number; repos: string[] }[] {
    const r = this.results();
    if (!r) return [];
    const map = new Map<string, { keys: Set<string>; refCount: number; repos: Set<string> }>();
    for (const [repoName, repo] of Object.entries(r.repos)) {
      for (const [projKey, info] of Object.entries(repo.projects)) {
        let entry = map.get(projKey);
        if (!entry) {
          entry = { keys: new Set(), refCount: 0, repos: new Set() };
          map.set(projKey, entry);
        }
        for (const k of info.issue_keys) entry.keys.add(k);
        entry.refCount += info.reference_count;
        entry.repos.add(repoName);
      }
    }
    return [...map.entries()]
      .map(([key, e]) => ({
        key,
        uniqueIssues: e.keys.size,
        refCount: e.refCount,
        repos: [...e.repos].sort(),
      }))
      .sort((a, b) => b.refCount - a.refCount);
  }

  // Project import methods

  isConfigured(key: string): boolean {
    return this.configuredKeys().has(key);
  }

  toggleProject(key: string) {
    if (this.isConfigured(key)) return;
    const s = new Set(this.projectSelection());
    if (s.has(key)) s.delete(key); else s.add(key);
    this.projectSelection.set(s);
  }

  selectAllProjects() {
    this.projectSelection.set(new Set(this.selectableProjects().map(p => p.key)));
  }

  selectNoProjects() {
    this.projectSelection.set(new Set());
  }

  verifySelected() {
    const keys = [...this.projectSelection()];
    if (keys.length === 0) return;
    this.verifying.set(true);
    this.message.set(null);
    this.settingsSvc.verifyProjects(keys).subscribe({
      next: res => {
        const m = new Map<string, VerifyProjectResult>();
        for (const p of res.projects) m.set(p.key, p);
        this.verifyResults.set(m);
        this.verified.set(true);
        this.verifying.set(false);
      },
      error: () => {
        this.verifying.set(false);
        this.message.set({ type: 'danger', text: 'Failed to verify projects. Check Atlassian connection.' });
      },
    });
  }

  validVerifiedCount(): number {
    let count = 0;
    for (const r of this.verifyResults().values()) {
      if (r.valid) count++;
    }
    return count;
  }

  addVerifiedToSettings() {
    const projects = [...this.verifyResults().values()]
      .filter(r => r.valid)
      .map(r => ({ key: r.key, name: r.name, board_id: null }));
    if (projects.length === 0) return;
    this.adding.set(true);
    this.message.set(null);
    this.settingsSvc.addJiraProjects(projects).subscribe({
      next: res => {
        this.adding.set(false);
        const newConfigured = new Set(this.configuredKeys());
        for (const k of res.added) newConfigured.add(k);
        this.configuredKeys.set(newConfigured);
        this.projectSelection.set(new Set());
        this.verifyResults.set(new Map());
        this.verified.set(false);
        this.message.set({
          type: 'success',
          text: `Added ${res.added.length} project(s) to settings. Total: ${res.total}.`,
        });
      },
      error: () => {
        this.adding.set(false);
        this.message.set({ type: 'danger', text: 'Failed to add projects to settings.' });
      },
    });
  }

  backToSelection() {
    this.verified.set(false);
    this.verifyResults.set(new Map());
  }
}
