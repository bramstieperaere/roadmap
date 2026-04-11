import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgTemplateOutlet } from '@angular/common';
import { ConfluenceService, ConfluenceSpace, ConfluencePageSummary } from '../../services/confluence';
import { ContextsService, RepoInfo, RepoTreeEntry } from '../../services/contexts';
import { WhisperTextarea } from '../../components/whisper-textarea/whisper-textarea';
import { getItemIcon, getItemTypeLabel } from '../context-utils';

interface ItemTypeEntry {
  type: string;
  label: string;
  icon: string;
  description: string;
}

export interface AddItemEvent {
  type: string;
  id: string;
  label?: string;
  text?: string;
}

interface RepoFileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children: RepoFileTreeNode[];
  loaded: boolean;
}

@Component({
  selector: 'app-add-item-panel',
  imports: [FormsModule, NgTemplateOutlet, WhisperTextarea],
  templateUrl: './add-item-panel.html',
  styleUrl: './add-item-panel.scss',
})
export class AddItemPanel {
  private confluenceService = inject(ConfluenceService);
  private service = inject(ContextsService);

  // Inputs (reference data loaded once by parent)
  spaces = input<ConfluenceSpace[]>([]);
  repos = input<RepoInfo[]>([]);
  mixinPaths = input<string[]>([]);
  adding = input(false);
  contextName = input('');

  // Outputs
  addItem = output<AddItemEvent>();
  cancel = output<void>();

  // Wizard
  step = signal<'type' | 'details' | 'confirm'>('type');

  itemTypes: ItemTypeEntry[] = [
    { type: 'confluence_page', label: 'Confluence Page', icon: getItemIcon('confluence_page'), description: 'Import a Confluence wiki page' },
    { type: 'jira_issue', label: 'Jira Issue', icon: getItemIcon('jira_issue'), description: 'Reference a Jira ticket' },
    { type: 'git_repo', label: 'Git Repository', icon: getItemIcon('git_repo'), description: 'Add a full git repository' },
    { type: 'repo_file', label: 'Repository File', icon: getItemIcon('repo_file'), description: 'Pick a specific file from a repo' },
    { type: 'bitbucket_pr', label: 'Bitbucket PR', icon: getItemIcon('bitbucket_pr'), description: 'Import a Bitbucket pull request' },
    { type: 'commits', label: 'Commits', icon: getItemIcon('commits'), description: 'Reference specific git commits' },
    { type: 'instructions', label: 'Instructions', icon: getItemIcon('instructions'), description: 'Add free-text instructions' },
    { type: 'scratch_dir', label: 'Scratch Directory', icon: getItemIcon('scratch_dir'), description: 'Link a scratch working directory' },
    { type: 'url', label: 'URL', icon: getItemIcon('url'), description: 'Fetch content from a web URL' },
    { type: 'logzio', label: 'Logz.io Log Search', icon: getItemIcon('logzio'), description: 'Query Logz.io for log entries' },
    { type: 'mixin', label: 'Mixin', icon: getItemIcon('mixin'), description: 'Include another context' },
  ];

  // Type filter
  typeFilter = signal('');
  filteredItemTypes = computed(() => {
    const q = this.typeFilter().toLowerCase().trim();
    if (!q) return this.itemTypes;
    return this.itemTypes.filter(t =>
      t.label.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
  });

  // Internal state
  mode = signal<string>('picking');
  itemLabel = signal('');
  error = signal('');

  // Confluence picker
  selectedSpaceKey = signal('');
  pageTree = signal<ConfluencePageSummary[]>([]);
  loadingPages = signal(false);
  expandedNodes = signal<Set<string>>(new Set());
  selectedPage = signal<{ id: string; title: string } | null>(null);
  pageFilter = signal('');

  filteredPageResults = computed(() => {
    const q = this.pageFilter().trim().toLowerCase();
    if (!q) return [];
    return this.flattenPages(this.pageTree()).filter(
      p => p.title.toLowerCase().includes(q) || p.id.includes(q)
    );
  });

  // Jira
  issueKey = signal('');

  // Instructions
  instructionsText = signal('');


  // Git repo
  selectedRepoName = signal('');
  repoFilter = signal('');

  filteredRepos = computed(() => {
    const q = this.repoFilter().trim().toLowerCase();
    const all = this.repos();
    if (!q) return all;
    return all.filter(
      r => r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q)
    );
  });

  // Repo file
  repoFileRepoName = signal('');
  repoFileRepoFilter = signal('');
  repoFilePath = signal('');
  repoTree = signal<RepoFileTreeNode[]>([]);
  repoTreeLoading = signal(false);
  repoTreeExpanded = signal<Set<string>>(new Set());

  filteredRepoFileRepos = computed(() => {
    const q = this.repoFileRepoFilter().trim().toLowerCase();
    const all = this.repos();
    if (!q) return all;
    return all.filter(
      r => r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q)
    );
  });

  // Scratch dir
  scratchPath = signal('');
  scratchConfigured = signal(false);
  scratchLoading = signal(false);

  // Bitbucket PR
  bitbucketPrUrl = signal('');
  bitbucketPrJson = signal('');
  bitbucketCommentsJson = signal('');

  bitbucketApiUrls = computed(() => {
    const url = this.bitbucketPrUrl().trim();
    const m = url.match(/^https?:\/\/bitbucket\.org\/([^/]+)\/([^/]+)\/pull-requests\/(\d+)/);
    if (!m) return null;
    const [, workspace, repo, id] = m;
    const base = `https://bitbucket.org/!api/2.0/repositories/${workspace}/${repo}/pullrequests/${id}`;
    return { pr: base, comments: `${base}/comments` };
  });

  // Commits
  commitsRepoName = signal('');
  commitsHashes = signal('');

  // URL
  urlValue = signal('');
  urlDescription = signal('');

  // Logz.io
  logzioQuery = signal('');
  logzioFromTime = signal('');
  logzioToTime = signal('');
  logzioSize = signal(50);

  // Mixin
  selectedMixinPath = signal('');

  // ── Type switching ──

  switchType(type: string) {
    this.mode.set(type);
    this.resetPickerState();
    if (type === 'scratch_dir') this.loadScratchPath();
  }

  private resetPickerState() {
    this.issueKey.set('');
    this.instructionsText.set('');
    this.selectedRepoName.set('');
    this.repoFilter.set('');
    this.selectedPage.set(null);
    this.repoFileRepoName.set('');
    this.repoFileRepoFilter.set('');
    this.repoFilePath.set('');
    this.bitbucketPrUrl.set('');
    this.bitbucketPrJson.set('');
    this.bitbucketCommentsJson.set('');
    this.itemLabel.set('');
    this.commitsRepoName.set('');
    this.commitsHashes.set('');
    this.urlValue.set('');
    this.urlDescription.set('');
    this.logzioQuery.set('');
    this.logzioFromTime.set('');
    this.logzioToTime.set('');
    this.logzioSize.set(50);
    this.selectedMixinPath.set('');
  }

  // ── Confluence page picker ──

  private flattenPages(pages: ConfluencePageSummary[]): ConfluencePageSummary[] {
    return pages.flatMap(p => [p, ...this.flattenPages(p.children)]);
  }

  selectSpace(spaceKey: string) {
    if (spaceKey === this.selectedSpaceKey()) return;
    this.selectedSpaceKey.set(spaceKey);
    this.pageTree.set([]);
    this.expandedNodes.set(new Set());
    this.selectedPage.set(null);
    this.pageFilter.set('');
    this.itemLabel.set('');
    if (!spaceKey) return;
    this.loadingPages.set(true);
    this.confluenceService.getPages(spaceKey).subscribe({
      next: (data) => { this.pageTree.set(data.pages); this.loadingPages.set(false); },
      error: () => this.loadingPages.set(false),
    });
  }

  toggleNode(id: string) {
    this.expandedNodes.update(set => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  isExpanded(id: string): boolean { return this.expandedNodes().has(id); }

  selectConfluencePage(pageId: string, pageTitle: string) {
    this.selectedPage.set({ id: pageId, title: pageTitle });
    this.itemLabel.set(pageTitle);
  }

  addConfluencePage() {
    const page = this.selectedPage();
    if (!page) return;
    this.addItem.emit({ type: 'confluence_page', id: page.id, label: this.itemLabel().trim() || page.title });
  }

  // ── Jira issue ──

  addJiraIssue() {
    const key = this.issueKey().trim().toUpperCase();
    if (!key) return;
    this.addItem.emit({ type: 'jira_issue', id: key, label: this.itemLabel().trim() || undefined });
  }

  onIssueKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addJiraIssue();
  }

  // ── Instructions ──

  addInstructions() {
    const text = this.instructionsText().trim();
    if (!text) return;
    const label = this.itemLabel().trim() || 'Instructions';
    this.addItem.emit({ type: 'instructions', id: label, label, text });
  }

  // ── Scratch dir ──

  private loadScratchPath() {
    const name = this.contextName();
    if (!name) return;
    this.scratchLoading.set(true);
    this.service.getScratchDir(name).subscribe({
      next: (res: { path: string; configured: boolean }) => {
        this.scratchPath.set(res.path);
        this.scratchConfigured.set(res.configured);
        this.itemLabel.set(name);
        this.scratchLoading.set(false);
      },
      error: () => this.scratchLoading.set(false),
    });
  }

  addScratchDir() {
    const path = this.scratchPath();
    if (!path) return;
    this.addItem.emit({ type: 'scratch_dir', id: path, label: this.itemLabel().trim() || this.contextName() });
  }

  // ── Git repo ──

  selectRepo(repoName: string) {
    this.selectedRepoName.set(repoName);
    this.itemLabel.set(repoName);
  }

  addGitRepo() {
    const repoName = this.selectedRepoName();
    if (!repoName) return;
    this.addItem.emit({ type: 'git_repo', id: repoName, label: this.itemLabel().trim() || repoName });
  }

  // ── Repo file ──

  clearRepoFileRepo() {
    this.repoFileRepoName.set('');
    this.repoFileRepoFilter.set('');
    this.repoFilePath.set('');
    this.itemLabel.set('');
    this.repoTree.set([]);
    this.repoTreeExpanded.set(new Set());
  }

  selectRepoFileRepo(repoName: string) {
    this.repoFileRepoName.set(repoName);
    this.repoFileRepoFilter.set('');
    this.repoFilePath.set('');
    this.itemLabel.set('');
    this.repoTree.set([]);
    this.repoTreeExpanded.set(new Set());
    if (!repoName) return;
    this.repoTreeLoading.set(true);
    this.service.getRepoTree(repoName).subscribe({
      next: (entries) => {
        this.repoTree.set(entries.map(e => ({ ...e, children: [], loaded: false })));
        this.repoTreeLoading.set(false);
      },
      error: () => this.repoTreeLoading.set(false),
    });
  }

  toggleRepoTreeNode(node: RepoFileTreeNode) {
    if (node.type !== 'dir') return;
    const expanded = this.repoTreeExpanded();
    if (expanded.has(node.path)) {
      this.repoTreeExpanded.update(s => { const n = new Set(s); n.delete(node.path); return n; });
      return;
    }
    this.repoTreeExpanded.update(s => new Set(s).add(node.path));
    if (node.loaded) return;
    this.service.getRepoTree(this.repoFileRepoName(), node.path).subscribe({
      next: (entries) => {
        const children = entries.map(e => ({ ...e, children: [] as RepoFileTreeNode[], loaded: false }));
        this.repoTree.update(tree => {
          const updated = structuredClone(tree);
          const target = this.findNode(updated, node.path);
          if (target) { target.children = children; target.loaded = true; }
          return updated;
        });
      },
    });
  }

  isRepoTreeExpanded(path: string): boolean { return this.repoTreeExpanded().has(path); }

  selectRepoFile(node: RepoFileTreeNode) {
    if (node.type !== 'file') return;
    this.repoFilePath.set(node.path);
    this.itemLabel.set(node.path);
  }

  private findNode(tree: RepoFileTreeNode[], path: string): RepoFileTreeNode | null {
    for (const n of tree) {
      if (n.path === path) return n;
      const found = this.findNode(n.children, path);
      if (found) return found;
    }
    return null;
  }

  addRepoFile() {
    const repoName = this.repoFileRepoName();
    const filePath = this.repoFilePath().trim().replace(/\\/g, '/');
    if (!repoName || !filePath) return;
    this.addItem.emit({ type: 'repo_file', id: `${repoName}:${filePath}`, label: this.itemLabel().trim() || filePath });
  }

  // ── Bitbucket PR ──

  importBitbucketPr() {
    const url = this.bitbucketPrUrl().trim();
    const prJson = this.bitbucketPrJson().trim();
    const commentsJson = this.bitbucketCommentsJson().trim();
    if (!url || !prJson || !commentsJson) return;
    this.error.set('');
    this.service.importBitbucketPr(url, prJson, commentsJson).subscribe({
      next: (res) => {
        this.addItem.emit({ type: 'bitbucket_pr', id: url, label: this.itemLabel().trim() || res.label });
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to import PR');
      },
    });
  }

  // ── Commits ──

  selectCommitsRepo(repoName: string) {
    this.commitsRepoName.set(repoName);
    this.commitsHashes.set('');
    this.itemLabel.set('');
  }

  addCommits() {
    const repo = this.commitsRepoName();
    const raw = this.commitsHashes().trim();
    if (!repo || !raw) return;
    const hashes = raw.split(/[\s,]+/).filter(h => h);
    if (!hashes.length) return;
    const id = `${repo}:${hashes.join(',')}`;
    const label = this.itemLabel().trim() || `${hashes.length} commit${hashes.length !== 1 ? 's' : ''}`;
    this.addItem.emit({ type: 'commits', id, label });
  }

  // ── Mixin ──

  selectMixinPath(path: string) {
    this.selectedMixinPath.set(path);
    this.itemLabel.set(path.split('/').pop() || path);
  }

  addMixin() {
    const path = this.selectedMixinPath();
    if (!path) return;
    this.addItem.emit({ type: 'mixin', id: path, label: this.itemLabel().trim() || path });
  }

  // ── URL ──

  addUrl() {
    const url = this.urlValue().trim();
    if (!url) return;
    const label = this.itemLabel().trim() || (url.length > 20 ? url.substring(0, 20) + '...' : url);
    const description = this.urlDescription().trim() || undefined;
    this.addItem.emit({ type: 'url', id: url, label, text: description });
  }

  // ── Logz.io ──

  addLogzioSearch() {
    const query = this.logzioQuery().trim();
    if (!query) return;
    const params = JSON.stringify({
      query,
      from_time: this.logzioFromTime() || undefined,
      to_time: this.logzioToTime() || undefined,
      size: this.logzioSize() || 50,
    });
    const label = this.itemLabel().trim() || `Logz.io: ${query.substring(0, 60)}`;
    this.addItem.emit({ type: 'logzio', id: query, label, text: params });
  }

  // ── Wizard navigation ──

  pickType(type: string) {
    this.switchType(type);
    this.typeFilter.set('');
    this.step.set('details');
  }

  goToStep(s: 'type' | 'details' | 'confirm') {
    if (s === 'details' && this.mode() === 'picking') return;
    if (s === 'confirm' && !this.canProceedToConfirm()) return;
    this.step.set(s);
  }

  prevStep() {
    if (this.step() === 'confirm') this.step.set('details');
    else if (this.step() === 'details') this.step.set('type');
  }

  canProceedToConfirm(): boolean {
    switch (this.mode()) {
      case 'confluence_page': return !!this.selectedPage();
      case 'jira_issue': return !!this.issueKey().trim();
      case 'git_repo': return !!this.selectedRepoName();
      case 'repo_file': return !!this.repoFilePath().trim();
      case 'bitbucket_pr': return !!this.bitbucketPrUrl().trim() && !!this.bitbucketPrJson().trim() && !!this.bitbucketCommentsJson().trim();
      case 'commits': return !!this.commitsRepoName() && !!this.commitsHashes().trim();
      case 'instructions': return !!this.instructionsText().trim();
      case 'scratch_dir': return !!this.scratchPath() && this.scratchConfigured();
      case 'url': return !!this.urlValue().trim();
      case 'logzio': return !!this.logzioQuery().trim();
      case 'mixin': return !!this.selectedMixinPath();
      default: return false;
    }
  }

  getSelectedTypeIcon(): string {
    return this.itemTypes.find(t => t.type === this.mode())?.icon || 'bi-file-text';
  }

  getSelectedTypeLabel(): string {
    return this.itemTypes.find(t => t.type === this.mode())?.label || this.mode();
  }

  getDetailsSummary(): string {
    switch (this.mode()) {
      case 'confluence_page': return this.selectedPage()?.title || '';
      case 'jira_issue': return this.issueKey().trim();
      case 'git_repo': return this.selectedRepoName();
      case 'repo_file': return `${this.repoFileRepoName()}:${this.repoFilePath()}`;
      case 'bitbucket_pr': return this.bitbucketPrUrl().trim();
      case 'commits': return `${this.commitsRepoName()}: ${this.commitsHashes().trim().split(/[\s,]+/).length} commit(s)`;
      case 'instructions': return this.instructionsText().trim().substring(0, 80) + (this.instructionsText().length > 80 ? '...' : '');
      case 'scratch_dir': return this.scratchPath();
      case 'url': return this.urlValue().trim();
      case 'logzio': return this.logzioQuery().trim();
      case 'mixin': return this.selectedMixinPath();
      default: return '';
    }
  }

  submitItem() {
    switch (this.mode()) {
      case 'confluence_page': this.addConfluencePage(); break;
      case 'jira_issue': this.addJiraIssue(); break;
      case 'git_repo': this.addGitRepo(); break;
      case 'repo_file': this.addRepoFile(); break;
      case 'bitbucket_pr': this.importBitbucketPr(); break;
      case 'commits': this.addCommits(); break;
      case 'instructions': this.addInstructions(); break;
      case 'scratch_dir': this.addScratchDir(); break;
      case 'url': this.addUrl(); break;
      case 'logzio': this.addLogzioSearch(); break;
      case 'mixin': this.addMixin(); break;
    }
  }
}
