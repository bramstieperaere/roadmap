import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgTemplateOutlet } from '@angular/common';
import { ConfluenceService, ConfluenceSpace, ConfluencePageSummary } from '../../services/confluence';
import { ContextsService, RepoInfo, RepoTreeEntry } from '../../services/contexts';
import { WhisperTextarea } from '../../components/whisper-textarea/whisper-textarea';

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

  // Outputs
  addItem = output<AddItemEvent>();
  cancel = output<void>();

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

  // Mixin
  selectedMixinPath = signal('');

  // ── Type switching ──

  switchType(type: string) {
    this.mode.set(type);
    this.resetPickerState();
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
}
