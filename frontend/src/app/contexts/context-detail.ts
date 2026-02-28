import { Component, computed, ElementRef, inject, Injector, OnInit, signal, ViewChild, afterNextRender, DestroyRef } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { NgTemplateOutlet } from '@angular/common';
import { CdkDropList, CdkDropListGroup, CdkDrag, CdkDragHandle, CdkDragDrop, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { ContextsService, ContextItem, ContextItemEntry, RepoInfo, PreviewSection, RepoTreeEntry, ContributingContext } from '../services/contexts';
import { ConfluenceService, ConfluenceSpace, ConfluencePageSummary } from '../services/confluence';

interface RepoFileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children: RepoFileTreeNode[];
  loaded: boolean;
}

@Component({
  selector: 'app-context-detail',
  imports: [FormsModule, RouterLink, NgTemplateOutlet, CdkDropListGroup, CdkDropList, CdkDrag, CdkDragHandle],
  templateUrl: './context-detail.html',
  styleUrl: './context-detail.scss',
})
export class ContextDetail implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private service = inject(ContextsService);
  private confluenceService = inject(ConfluenceService);
  private destroyRef = inject(DestroyRef);
  private injector = inject(Injector);

  // Route params
  name = signal('');
  child = signal<string | null>(null);

  // Context data
  context = signal<ContextItem | null>(null);
  loading = signal(false);
  error = signal('');

  /** Contexts that contribute to the currently viewed context. */
  contributingChildren = computed(() => {
    const ctx = this.context();
    const childName = this.child();
    if (!ctx) return [];
    // Child route: the parent contributes (via "parent" item), show only this child
    if (childName) {
      return ctx.children.filter(c => c.name === childName);
    }
    // Top-level context: no children contribute to the parent
    return [];
  });

  // Edit mode
  editing = signal(false);
  editingName = signal(false);
  editNameValue = signal('');
  editingChildName = signal(false);
  editChildNameValue = signal('');

  // Inline item editing (instructions)
  editingItemKey = signal<string | null>(null);
  editItemLabel = signal('');
  editItemText = signal('');

  // Expanded child
  expandedChild = signal<string | null>(null);

  // Add item mode
  addItemMode = signal<string | null>(null);
  addingItem = signal(false);
  itemLabel = signal('');
  childAddItemMode = signal<string | null>(null);

  // Confluence picker
  spaces = signal<ConfluenceSpace[]>([]);
  selectedSpaceKey = signal('');
  pageTree = signal<ConfluencePageSummary[]>([]);
  loadingPages = signal(false);
  expandedNodes = signal<Set<string>>(new Set());
  selectedPage = signal<{ id: string; title: string } | null>(null);

  // Jira
  issueKey = signal('');

  // Instructions
  instructionsText = signal('');

  // Git repo
  repos = signal<RepoInfo[]>([]);
  selectedRepoName = signal('');

  // Repo file
  repoFileRepoName = signal('');
  repoFilePath = signal('');
  repoTree = signal<RepoFileTreeNode[]>([]);
  repoTreeLoading = signal(false);
  repoTreeExpanded = signal<Set<string>>(new Set());

  // Mixin
  availableContextPaths = signal<string[]>([]);
  selectedMixinPath = signal('');
  contributingMixins = signal<ContributingContext[]>([]);
  showContributing = signal(true);

  filteredMixinPaths = computed(() => {
    const all = this.availableContextPaths();
    const name = this.name();
    const childName = this.child();
    const selfPath = childName ? `${name}/${childName}` : name;
    const ctx = this.context();

    const existing = new Set<string>();
    if (ctx) {
      for (const item of ctx.items) {
        if (item.type === 'mixin') existing.add(item.id);
      }
      for (const c of ctx.children) {
        for (const item of c.items) {
          if (item.type === 'mixin') existing.add(item.id);
        }
      }
    }

    return all.filter(p => p !== selfPath && p !== name && !existing.has(p));
  });

  // Sub-context add
  addingChild = signal(false);
  newChildName = signal('');

  // Preview
  previewSections = signal<PreviewSection[]>([]);
  previewLoading = signal(false);
  selectedItemKeys = signal<Set<string>>(new Set());
  primaryClickedKey = signal<string | null>(null);
  showDelimiters = signal(false);

  tocContent = computed(() => {
    const sections = this.previewSections();
    if (sections.length === 0) return '';
    const name = this.child() ? `${this.name()}/${this.child()}` : this.name();
    const n = sections.length;
    const lines: string[] = [
      `# Context: ${name}`,
      '',
      `This context contains ${n} item${n !== 1 ? 's' : ''}:`,
    ];
    for (let i = 0; i < n; i++) {
      lines.push(`${i + 1}. [${this.getItemTypeLabel(sections[i].type)}] ${sections[i].label}`);
    }
    lines.push('');
    lines.push(
      `Each item is delimited by ######### <item name> BEGIN ######### `
      + `and ######### <item name> END #########. `
      + `For example: ######### ${sections[0].label} BEGIN #########`
    );
    return lines.join('\n');
  });

  @ViewChild('nameInput') nameInput?: ElementRef<HTMLInputElement>;
  @ViewChild('childNameInput') childNameInput?: ElementRef<HTMLInputElement>;

  ngOnInit() {
    this.loadSpaces();
    this.loadRepos();
    this.loadContextPaths();

    this.route.params.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      this.name.set(params['name'] || '');
      const child = params['child'] || null;
      this.child.set(child);
      this.expandedChild.set(child);
      this.editing.set(false);
      this.editingName.set(false);
      this.editingChildName.set(false);
      this.addItemMode.set(null);
      this.childAddItemMode.set(null);
      this.loadContext();
    });
  }

  // ── Context loading ──

  private loadContext() {
    const name = this.name();
    if (!name) return;
    this.loading.set(true);
    this.service.get(name).subscribe({
      next: (ctx) => {
        this.context.set(ctx);
        this.loading.set(false);
        this.refreshPreview();
        this.loadContributing();
      },
      error: () => this.loading.set(false),
    });
  }

  private reloadContext() {
    const name = this.name();
    if (!name) return;
    this.service.get(name).subscribe({
      next: (ctx) => {
        this.context.set(ctx);
        this.loadContributing();
      },
    });
  }

  private loadSpaces() {
    this.confluenceService.getSpaces().subscribe({
      next: (spaces) => this.spaces.set(spaces),
      error: () => {},
    });
  }

  private loadRepos() {
    this.service.getRepositories().subscribe({
      next: (repos) => this.repos.set(repos),
      error: () => {},
    });
  }

  private loadContextPaths() {
    this.service.getAllPaths().subscribe({
      next: (paths) => this.availableContextPaths.set(paths),
      error: () => {},
    });
  }

  private loadContributing() {
    const name = this.name();
    const child = this.child();
    if (!name) return;
    const obs = child
      ? this.service.getContributingChild(name, child)
      : this.service.getContributing(name);
    obs.subscribe({
      next: (mixins) => this.contributingMixins.set(mixins),
      error: () => this.contributingMixins.set([]),
    });
  }

  // ── Delete context ──

  deleteContext() {
    const name = this.name();
    if (!name) return;
    this.service.remove(name).subscribe({
      next: () => this.router.navigate(['/contexts']),
      error: (err) => {
        if (err.status === 409 && err.error?.detail?.usages) {
          const usages = err.error.detail.usages as string[];
          if (confirm(`This context is referenced as a mixin by: ${usages.join(', ')}. Delete anyway?`)) {
            this.service.remove(name, true).subscribe({
              next: () => this.router.navigate(['/contexts']),
              error: (e) => this.error.set(e.error?.detail || 'Failed to delete context'),
            });
          }
        } else {
          this.error.set(err.error?.detail || 'Failed to delete context');
        }
      },
    });
  }

  // ── Add item ──

  showAddItem(mode: string) {
    this.addItemMode.set(mode);
    this.resetPickerState();
  }

  switchItemType(type: string) {
    this.addItemMode.set(type);
    this.resetPickerState();
  }

  cancelAddItem() {
    this.addItemMode.set(null);
  }

  private resetPickerState() {
    this.issueKey.set('');
    this.instructionsText.set('');
    this.selectedRepoName.set('');
    this.selectedPage.set(null);
    this.repoFileRepoName.set('');
    this.repoFilePath.set('');
    this.itemLabel.set('');
    this.selectedMixinPath.set('');
  }

  // Confluence page picker
  selectSpace(spaceKey: string) {
    if (spaceKey === this.selectedSpaceKey()) return;
    this.selectedSpaceKey.set(spaceKey);
    this.pageTree.set([]);
    this.expandedNodes.set(new Set());
    this.selectedPage.set(null);
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
    const label = this.itemLabel().trim() || page.title;
    this.addItemCall('confluence_page', page.id, label);
  }

  // Jira issue
  addJiraIssue() {
    const key = this.issueKey().trim().toUpperCase();
    if (!key) return;
    this.addItemCall('jira_issue', key, this.itemLabel().trim() || undefined);
  }

  onIssueKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addJiraIssue();
  }

  // Instructions
  addInstructions() {
    const text = this.instructionsText().trim();
    const label = this.itemLabel().trim();
    if (!label) return;
    this.addItemCall('instructions', label, label, text);
  }

  // Git repo
  selectRepo(repoName: string) {
    this.selectedRepoName.set(repoName);
    this.itemLabel.set(repoName);
  }

  addGitRepo() {
    const repoName = this.selectedRepoName();
    if (!repoName) return;
    this.addItemCall('git_repo', repoName, this.itemLabel().trim() || repoName);
  }

  // Repo file
  selectRepoFileRepo(repoName: string) {
    this.repoFileRepoName.set(repoName);
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
    this.addItemCall('repo_file', `${repoName}:${filePath}`, this.itemLabel().trim() || filePath);
  }

  // Mixin
  selectMixinPath(path: string) {
    this.selectedMixinPath.set(path);
    this.itemLabel.set(path.split('/').pop() || path);
  }

  addMixin() {
    const path = this.selectedMixinPath();
    if (!path) return;
    this.addItemCall('mixin', path, this.itemLabel().trim() || path);
  }

  addChildMixin() {
    const path = this.selectedMixinPath();
    if (!path) return;
    this.addChildItemCall('mixin', path, this.itemLabel().trim() || path);
  }

  private addItemCall(type: string, id: string, label?: string, text?: string) {
    this.addingItem.set(true);
    this.error.set('');
    this.service.addItem(this.name(), type, id, label, text).subscribe({
      next: () => {
        this.addingItem.set(false);
        this.addItemMode.set(null);
        this.reloadContext();
        this.refreshPreview();
      },
      error: (err) => {
        this.addingItem.set(false);
        this.error.set(err.error?.detail || 'Failed to add item');
      },
    });
  }

  // ── Remove item ──

  removeItem(item: ContextItemEntry) {
    this.service.removeItem(this.name(), item.type, item.id).subscribe({
      next: () => { this.reloadContext(); this.refreshPreview(); },
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove item'),
    });
  }

  // ── Edit item (instructions) ──

  startEditItem(item: ContextItemEntry) {
    if (!this.editing()) return;
    this.editingItemKey.set(this.itemKey(item));
    this.editItemLabel.set(item.label || item.title);
    this.editItemText.set(item.text || '');
  }

  cancelEditItem() {
    this.editingItemKey.set(null);
  }

  saveItem(item: ContextItemEntry, childName?: string) {
    const label = this.editItemLabel().trim();
    const text = this.editItemText().trim();
    if (!label) return;
    const body: { label?: string; text?: string } = { label };
    if (item.type === 'instructions') body.text = text;

    const obs = childName
      ? this.service.updateChildItem(this.name(), childName, item.type, item.id, body)
      : this.service.updateItem(this.name(), item.type, item.id, body);
    obs.subscribe({
      next: () => {
        this.editingItemKey.set(null);
        this.reloadContext();
        this.refreshPreview();
      },
      error: (err) => this.error.set(err.error?.detail || 'Failed to update item'),
    });
  }

  onEditItemKeydown(event: KeyboardEvent, item: ContextItemEntry, childName?: string) {
    if (event.key === 'Escape') this.cancelEditItem();
  }

  // ── Sub-contexts ──

  addChild() {
    const childName = this.newChildName().trim();
    if (!childName) return;
    this.addingChild.set(true);
    this.error.set('');
    this.service.addChild(this.name(), childName).subscribe({
      next: () => {
        this.addingChild.set(false);
        this.newChildName.set('');
        this.reloadContext();
      },
      error: (err) => {
        this.addingChild.set(false);
        this.error.set(err.error?.detail || 'Failed to add sub-context');
      },
    });
  }

  removeChild(childName: string) {
    this.service.removeChild(this.name(), childName).subscribe({
      next: () => {
        if (this.expandedChild() === childName) {
          this.expandedChild.set(null);
          this.refreshPreview();
        }
        this.reloadContext();
        this.loadContextPaths();
      },
      error: (err) => {
        if (err.status === 409 && err.error?.detail?.usages) {
          const usages = err.error.detail.usages as string[];
          if (confirm(`This sub-context is referenced as a mixin by: ${usages.join(', ')}. Delete anyway?`)) {
            this.service.removeChild(this.name(), childName, true).subscribe({
              next: () => {
                if (this.expandedChild() === childName) {
                  this.expandedChild.set(null);
                  this.refreshPreview();
                }
                this.reloadContext();
                this.loadContextPaths();
              },
              error: (e) => this.error.set(e.error?.detail || 'Failed to remove sub-context'),
            });
          }
        } else {
          this.error.set(err.error?.detail || 'Failed to remove sub-context');
        }
      },
    });
  }

  toggleChild(childName: string) {
    this.editingChildName.set(false);
    if (this.expandedChild() === childName) {
      this.expandedChild.set(null);
      this.childAddItemMode.set(null);
      this.loadPreview(this.name());
    } else {
      this.expandedChild.set(childName);
      this.childAddItemMode.set(null);
      this.loadChildPreview(this.name(), childName);
    }
  }

  showChildAddItem(mode: string) {
    this.childAddItemMode.set(mode);
    this.resetPickerState();
  }

  showChildAddItemFor(childName: string, mode: string) {
    this.expandedChild.set(childName);
    this.childAddItemMode.set(mode);
    this.resetPickerState();
  }

  switchChildItemType(childName: string, type: string) {
    this.expandedChild.set(childName);
    this.childAddItemMode.set(type);
    this.resetPickerState();
  }

  cancelChildAddItem() {
    this.childAddItemMode.set(null);
  }

  addChildConfluencePage() {
    const page = this.selectedPage();
    if (!page) return;
    this.addChildItemCall('confluence_page', page.id, this.itemLabel().trim() || page.title);
  }

  addChildJiraIssue() {
    const key = this.issueKey().trim().toUpperCase();
    if (!key) return;
    this.addChildItemCall('jira_issue', key, this.itemLabel().trim() || undefined);
  }

  addChildGitRepo() {
    const repoName = this.selectedRepoName();
    if (!repoName) return;
    this.addChildItemCall('git_repo', repoName, this.itemLabel().trim() || repoName);
  }

  addChildRepoFile() {
    const repoName = this.repoFileRepoName();
    const filePath = this.repoFilePath().trim().replace(/\\/g, '/');
    if (!repoName || !filePath) return;
    this.addChildItemCall('repo_file', `${repoName}:${filePath}`, this.itemLabel().trim() || filePath);
  }

  addChildInstructions() {
    const text = this.instructionsText().trim();
    const label = this.itemLabel().trim();
    if (!label) return;
    this.addChildItemCall('instructions', label, label, text);
  }

  private addChildItemCall(type: string, id: string, label?: string, text?: string) {
    const childName = this.expandedChild();
    if (!childName) return;
    this.addingItem.set(true);
    this.error.set('');
    this.service.addChildItem(this.name(), childName, type, id, label, text).subscribe({
      next: () => {
        this.addingItem.set(false);
        this.childAddItemMode.set(null);
        this.reloadContext();
        this.refreshPreview();
      },
      error: (err) => {
        this.addingItem.set(false);
        this.error.set(err.error?.detail || 'Failed to add item');
      },
    });
  }

  removeChildItem(childName: string, item: ContextItemEntry) {
    this.service.removeChildItem(this.name(), childName, item.type, item.id).subscribe({
      next: () => { this.reloadContext(); this.refreshPreview(); },
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove item'),
    });
  }

  onChildKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addChild();
  }

  // ── Preview ──

  private loadPreview(name: string) {
    this.previewLoading.set(true);
    this.previewSections.set([]);
    this.service.getPreview(name).subscribe({
      next: (sections) => { this.previewSections.set(sections); this.previewLoading.set(false); },
      error: () => { this.previewSections.set([]); this.previewLoading.set(false); },
    });
  }

  private loadChildPreview(parentName: string, childName: string) {
    this.previewLoading.set(true);
    this.previewSections.set([]);
    this.service.getChildPreview(parentName, childName).subscribe({
      next: (sections) => { this.previewSections.set(sections); this.previewLoading.set(false); },
      error: () => { this.previewSections.set([]); this.previewLoading.set(false); },
    });
  }

  refreshPreview() {
    const name = this.name();
    if (!name) return;
    const child = this.expandedChild();
    if (child) {
      this.loadChildPreview(name, child);
    } else {
      this.loadPreview(name);
    }
  }

  itemKey(item: { type: string; id: string }): string {
    return `${item.type}::${item.id}`;
  }

  isParentSelected(): boolean {
    const ctx = this.context();
    const keys = this.selectedItemKeys();
    if (!ctx || keys.size === 0) return false;
    return ctx.items.some(i => keys.has(this.itemKey(i)));
  }

  isMixinSelected(mixinId: string): boolean {
    const keys = this.selectedItemKeys();
    if (keys.size === 0) return false;
    const mixinKeys = this.collectMixinPreviewKeys(mixinId);
    for (const k of mixinKeys) {
      if (keys.has(k)) return true;
    }
    return false;
  }

  /** Check whether the "parent" item inside a contributing mixin card is selected. */
  isContributingParentSelected(contributingPath: string): boolean {
    const keys = this.selectedItemKeys();
    if (keys.size === 0) return false;
    const parentPath = contributingPath.split('/')[0];
    const parentCtx = this.contributingMixins().find(m => m.path === parentPath);
    if (!parentCtx) return false;
    return parentCtx.items.some(i => i.type !== 'parent' && i.type !== 'mixin' && keys.has(this.itemKey(i)));
  }

  private selectAndScroll(clickedKey: string, keys: Set<string>) {
    this.primaryClickedKey.set(clickedKey);
    this.selectedItemKeys.set(keys);
    const scrollTo = keys.has(clickedKey) ? clickedKey : (keys.size > 0 ? keys.values().next().value : null);
    if (scrollTo) {
      const el = document.getElementById('preview-' + scrollTo);
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  scrollToPreview(item: ContextItemEntry) {
    const clicked = this.itemKey(item);
    if (item.type === 'parent') {
      const ctx = this.context();
      if (!ctx) return;
      this.selectAndScroll(clicked, new Set(ctx.items.map(i => this.itemKey(i))));
    } else if (item.type === 'mixin') {
      this.selectAndScroll(clicked, this.collectMixinPreviewKeys(item.id));
    } else {
      this.selectAndScroll(clicked, new Set([clicked]));
    }
  }

  /** Handle click on an item inside a contributing mixin card. */
  scrollToContributingItem(item: ContextItemEntry, contributingPath: string) {
    const clicked = this.itemKey(item);
    if (item.type === 'parent') {
      // "Parent" here refers to the mixin context's own parent, not the
      // current context's parent.  Find the parent contributing context.
      const parentPath = contributingPath.split('/')[0];
      const parentCtx = this.contributingMixins().find(m => m.path === parentPath);
      if (parentCtx) {
        const keys = new Set<string>();
        for (const pi of parentCtx.items) {
          if (pi.type === 'mixin') {
            for (const k of this.collectMixinPreviewKeys(pi.id)) keys.add(k);
          } else if (pi.type !== 'parent') {
            keys.add(this.itemKey(pi));
          }
        }
        this.selectAndScroll(clicked, keys);
      }
    } else if (item.type === 'mixin') {
      this.selectAndScroll(clicked, this.collectMixinPreviewKeys(item.id));
    } else {
      this.selectAndScroll(clicked, new Set([clicked]));
    }
  }

  /** Collect all preview item keys produced by a mixin path, recursively. */
  private collectMixinPreviewKeys(mixinPath: string): Set<string> {
    const keys = new Set<string>();
    const mixinMap = new Map<string, ContributingContext>();
    for (const m of this.contributingMixins()) {
      mixinMap.set(m.path, m);
    }

    const visited = new Set<string>();
    const collect = (path: string) => {
      if (visited.has(path)) return;
      visited.add(path);

      // If this is a child path, its parent also contributes
      const parts = path.split('/');
      if (parts.length > 1) {
        const parentPath = parts[0];
        if (!visited.has(parentPath)) {
          visited.add(parentPath);
          const parentCtx = mixinMap.get(parentPath);
          if (parentCtx) {
            for (const pi of parentCtx.items) {
              if (pi.type === 'mixin') collect(pi.id);
              else if (pi.type !== 'parent') keys.add(this.itemKey(pi));
            }
          }
        }
      }

      const ctx = mixinMap.get(path);
      if (!ctx) return;
      for (const ci of ctx.items) {
        if (ci.type === 'mixin') collect(ci.id);
        else if (ci.type !== 'parent') keys.add(this.itemKey(ci));
      }
    };

    collect(mixinPath);
    return keys;
  }

  // ── Edit mode ──

  toggleEdit() {
    const wasEditing = this.editing();
    this.editing.set(!wasEditing);
    if (wasEditing) {
      this.editingName.set(false);
      this.editingChildName.set(false);
      this.editingItemKey.set(null);
      this.addItemMode.set(null);
      this.childAddItemMode.set(null);
      this.addingChild.set(false);
    }
  }

  startEditName() {
    if (!this.editing()) return;
    const ctx = this.context();
    if (!ctx) return;
    this.editNameValue.set(ctx.name);
    this.editingName.set(true);
    afterNextRender(() => {
      this.nameInput?.nativeElement.focus();
      this.nameInput?.nativeElement.select();
    }, { injector: this.injector });
  }

  saveName() {
    const oldName = this.name();
    const newName = this.editNameValue().trim();
    if (!newName || newName === oldName) {
      this.editingName.set(false);
      return;
    }
    this.service.rename(oldName, newName).subscribe({
      next: () => {
        this.editingName.set(false);
        this.router.navigate(['/contexts', newName], { replaceUrl: true });
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to rename context');
        this.editingName.set(false);
      },
    });
  }

  cancelEditName() { this.editingName.set(false); }

  onNameKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.saveName();
    else if (event.key === 'Escape') this.cancelEditName();
  }

  startEditChildName(childName?: string) {
    if (!this.editing()) return;
    const name = childName || this.expandedChild();
    if (!name) return;
    this.expandedChild.set(name);
    this.editChildNameValue.set(name);
    this.editingChildName.set(true);
    afterNextRender(() => {
      this.childNameInput?.nativeElement.focus();
      this.childNameInput?.nativeElement.select();
    }, { injector: this.injector });
  }

  saveChildName() {
    const parentName = this.name();
    const oldName = this.expandedChild();
    const newName = this.editChildNameValue().trim();
    if (!oldName || !newName || newName === oldName) {
      this.editingChildName.set(false);
      return;
    }
    this.service.renameChild(parentName, oldName, newName).subscribe({
      next: () => {
        this.expandedChild.set(newName);
        this.editingChildName.set(false);
        this.reloadContext();
        this.loadChildPreview(parentName, newName);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to rename sub-context');
        this.editingChildName.set(false);
      },
    });
  }

  cancelEditChildName() { this.editingChildName.set(false); }

  onChildNameKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.saveChildName();
    else if (event.key === 'Escape') this.cancelEditChildName();
  }

  // ── Drag and drop ──

  onDrop(event: CdkDragDrop<string>) {
    const ctxName = this.name();
    const sourceId: string = event.previousContainer.data;
    const targetId: string = event.container.data;

    if (event.previousContainer === event.container) {
      if (event.previousIndex === event.currentIndex) return;
      const listRef = this.getItemsList(sourceId);
      if (!listRef) return;
      moveItemInArray(listRef, event.previousIndex, event.currentIndex);
      this.context.update(c => c ? { ...c } : c);

      const childName = sourceId === 'parent' ? null : sourceId.replace('child:', '');
      const reorder$ = childName
        ? this.service.reorderChildItems(ctxName, childName, listRef.map(i => ({ type: i.type, id: i.id })))
        : this.service.reorderItems(ctxName, listRef.map(i => ({ type: i.type, id: i.id })));
      reorder$.subscribe({
        next: () => this.refreshPreview(),
        error: () => this.reloadContext(),
      });
    } else {
      const srcList = this.getItemsList(sourceId);
      const dstList = this.getItemsList(targetId);
      if (!srcList || !dstList) return;

      const item = srcList[event.previousIndex];
      transferArrayItem(srcList, dstList, event.previousIndex, event.currentIndex);
      this.context.update(c => c ? { ...c } : c);

      const fromChild = sourceId === 'parent' ? null : sourceId.replace('child:', '');
      const toChild = targetId === 'parent' ? null : targetId.replace('child:', '');
      this.service.moveItem(ctxName, {
        type: item.type, id: item.id,
        from_child: fromChild, to_child: toChild,
        to_index: event.currentIndex,
      }).subscribe({
        next: () => this.refreshPreview(),
        error: () => this.reloadContext(),
      });
    }
  }

  private getItemsList(listId: string): ContextItemEntry[] | null {
    const ctx = this.context();
    if (!ctx) return null;
    if (listId === 'parent') return ctx.items;
    const childName = listId.replace('child:', '');
    return ctx.children?.find(c => c.name === childName)?.items ?? null;
  }

  // ── Helpers ──

  dismissError() { this.error.set(''); }

  getItemIcon(type: string): string {
    switch (type) {
      case 'confluence_page': return 'bi-journal-text';
      case 'jira_issue': return 'bi-bug';
      case 'instructions': return 'bi-chat-left-text';
      case 'git_repo': return 'bi-git';
      case 'repo_file': return 'bi-file-code';
      case 'parent': return 'bi-box-arrow-in-up';
      case 'mixin': return 'bi-box-arrow-in-right';
      default: return 'bi-file-text';
    }
  }

  getItemTypeLabel(type: string): string {
    switch (type) {
      case 'confluence_page': return 'Confluence';
      case 'jira_issue': return 'Jira';
      case 'instructions': return 'Instructions';
      case 'git_repo': return 'Git Repo';
      case 'repo_file': return 'File';
      case 'parent': return 'Parent';
      case 'mixin': return 'Mixin';
      default: return type;
    }
  }

  getSectionContent(section: PreviewSection): string {
    if (!this.showDelimiters()) return section.content;
    return `######### ${section.label} BEGIN #########\n\n${section.content}\n\n######### ${section.label} END #########`;
  }
}
