import { Component, computed, ElementRef, inject, Injector, OnInit, signal, ViewChild, afterNextRender, DestroyRef } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { CdkDropList, CdkDropListGroup, CdkDrag, CdkDragDrop, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { ContextsService, ContextItem, ContextItemEntry, RepoInfo, PreviewSection, ContributingContext } from '../services/contexts';
import { ConfluenceService, ConfluenceSpace } from '../services/confluence';
import { ConfirmDialogService } from '../components/confirm-dialog/confirm-dialog.service';
import { AddItemPanel, AddItemEvent } from './add-item-panel/add-item-panel';
import { PreviewPanel } from './preview-panel/preview-panel';
import { ContextChat, ChatAddItemEvent, ChatTagEvent, ChatDescriptionEvent } from './context-chat/context-chat';
import { itemKey, getItemIcon, getItemTypeLabel, getItemDisplayId } from './context-utils';

@Component({
  selector: 'app-context-detail',
  imports: [FormsModule, RouterLink, CdkDropListGroup, CdkDropList, CdkDrag, AddItemPanel, PreviewPanel, ContextChat],
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
  private confirmDialog = inject(ConfirmDialogService);

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
  editingDescription = signal(false);
  editDescriptionValue = signal('');
  editingChildName = signal(false);
  editChildNameValue = signal('');
  editingChildDescription = signal(false);
  editChildDescriptionValue = signal('');

  // Inline item editing (instructions)
  editingItemKey = signal<string | null>(null);
  editItemLabel = signal('');
  editItemText = signal('');

  // Expanded child
  expandedChild = signal<string | null>(null);

  // Tags
  newTag = signal('');
  addingTag = signal(false);

  // Add item mode
  showParentAddPanel = signal(false);
  showChildAddPanel = signal(false);
  parentAdding = signal(false);
  childAdding = signal(false);

  // Reference data for add-item-panel
  spaces = signal<ConfluenceSpace[]>([]);
  repos = signal<RepoInfo[]>([]);

  // Mixin
  availableContextPaths = signal<string[]>([]);
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
      this.showParentAddPanel.set(false);
      this.showChildAddPanel.set(false);
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

  async deleteContext() {
    const name = this.name();
    if (!name) return;
    const ok = await this.confirmDialog.open({
      title: 'Delete context',
      message: `Delete "${name}" and all its sub-contexts?`,
    });
    if (!ok) return;
    this.service.remove(name).subscribe({
      next: () => this.router.navigate(['/contexts']),
      error: (err) => {
        if (err.status === 409 && err.error?.detail?.usages) {
          const usages = err.error.detail.usages as string[];
          this.confirmDialog.open({
            title: 'Mixin references',
            message: `This context is referenced as a mixin by: ${usages.join(', ')}. Delete anyway?`,
          }).then(force => {
            if (!force) return;
            this.service.remove(name, true).subscribe({
              next: () => this.router.navigate(['/contexts']),
              error: (e) => this.error.set(e.error?.detail || 'Failed to delete context'),
            });
          });
        } else {
          this.error.set(err.error?.detail || 'Failed to delete context');
        }
      },
    });
  }

  // Chat assistant
  showChat = signal(false);

  chatExistingItems = computed(() => {
    const ctx = this.context();
    if (!ctx) return [];
    return ctx.items.map(i => ({ type: i.type, id: i.id, label: i.label || i.title }));
  });

  onChatAddItem(event: ChatAddItemEvent) {
    this.error.set('');
    this.service.addItem(this.name(), event.type, event.id, event.label, event.text).subscribe({
      next: () => { this.reloadContext(); this.refreshPreview(); },
      error: (err) => this.error.set(err.error?.detail || 'Failed to add item'),
    });
  }

  onChatTag(event: ChatTagEvent) {
    const ctx = this.context();
    if (!ctx) return;
    const tags = [...(ctx.tags ?? [])];
    if (event.action === 'add' && !tags.includes(event.tag)) {
      tags.push(event.tag);
    } else if (event.action === 'remove') {
      const idx = tags.indexOf(event.tag);
      if (idx >= 0) tags.splice(idx, 1);
    } else return;
    this.service.updateTags(this.name(), tags).subscribe({
      next: (updated) => this.context.set(updated),
      error: (err) => this.error.set(err.error?.detail || 'Failed to update tags'),
    });
  }

  onChatDescription(event: ChatDescriptionEvent) {
    this.service.updateDescription(this.name(), event.description).subscribe({
      next: (updated) => this.context.set(updated),
      error: (err) => this.error.set(err.error?.detail || 'Failed to update description'),
    });
  }

  // ── Add item ──

  onParentAddItem(event: AddItemEvent) {
    this.parentAdding.set(true);
    this.error.set('');
    this.service.addItem(this.name(), event.type, event.id, event.label, event.text).subscribe({
      next: () => {
        this.parentAdding.set(false);
        this.showParentAddPanel.set(false);
        this.reloadContext();
        this.refreshPreview();
      },
      error: (err) => {
        this.parentAdding.set(false);
        this.error.set(err.error?.detail || 'Failed to add item');
      },
    });
  }

  onChildAddItem(event: AddItemEvent) {
    const childName = this.expandedChild();
    if (!childName) return;
    this.childAdding.set(true);
    this.error.set('');
    this.service.addChildItem(this.name(), childName, event.type, event.id, event.label, event.text).subscribe({
      next: () => {
        this.childAdding.set(false);
        this.showChildAddPanel.set(false);
        this.reloadContext();
        this.refreshPreview();
      },
      error: (err) => {
        this.childAdding.set(false);
        this.error.set(err.error?.detail || 'Failed to add item');
      },
    });
  }

  // ── Remove item ──

  async removeItem(item: ContextItemEntry) {
    const ok = await this.confirmDialog.open({
      title: 'Remove item',
      message: `Remove "${item.label || item.title}" from this context?`,
    });
    if (!ok) return;
    this.service.removeItem(this.name(), item.type, item.id).subscribe({
      next: () => { this.reloadContext(); this.refreshPreview(); },
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove item'),
    });
  }

  // ── Edit item (instructions) ──

  startEditItem(item: ContextItemEntry) {
    if (!this.editing() || item.type === 'inquiry') return;
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

  onEditItemKeydown(event: KeyboardEvent, _item: ContextItemEntry, _childName?: string) {
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

  async removeChild(childName: string) {
    const ok = await this.confirmDialog.open({
      title: 'Delete sub-context',
      message: `Delete sub-context "${childName}"?`,
    });
    if (!ok) return;
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
          this.confirmDialog.open({
            title: 'Mixin references',
            message: `This sub-context is referenced as a mixin by: ${usages.join(', ')}. Delete anyway?`,
          }).then(force => {
            if (!force) return;
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
          });
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
      this.showChildAddPanel.set(false);
      this.loadPreview(this.name());
    } else {
      this.expandedChild.set(childName);
      this.showChildAddPanel.set(false);
      this.loadChildPreview(this.name(), childName);
    }
  }

  showChildAddItemFor(childName: string) {
    this.expandedChild.set(childName);
    this.showChildAddPanel.set(true);
  }

  async removeChildItem(childName: string, item: ContextItemEntry) {
    const ok = await this.confirmDialog.open({
      title: 'Remove item',
      message: `Remove "${item.label || item.title}" from "${childName}"?`,
    });
    if (!ok) return;
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

  itemKey = itemKey;

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
      this.editingDescription.set(false);
      this.editingChildName.set(false);
      this.editingItemKey.set(null);
      this.showParentAddPanel.set(false);
      this.showChildAddPanel.set(false);
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

  startEditDescription() {
    if (!this.editing()) return;
    const ctx = this.context();
    if (!ctx) return;
    this.editDescriptionValue.set(ctx.description || '');
    this.editingDescription.set(true);
  }

  saveDescription() {
    const name = this.name();
    const desc = this.editDescriptionValue().trim();
    const ctx = this.context();
    if (!ctx || desc === (ctx.description || '')) {
      this.editingDescription.set(false);
      return;
    }
    this.service.updateDescription(name, desc).subscribe({
      next: (updated) => {
        this.context.set(updated);
        this.editingDescription.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to update description');
        this.editingDescription.set(false);
      },
    });
  }

  cancelEditDescription() { this.editingDescription.set(false); }

  onDescriptionKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.saveDescription();
    else if (event.key === 'Escape') this.cancelEditDescription();
  }

  addTag() {
    const tag = this.newTag().trim();
    this.newTag.set('');
    this.addingTag.set(false);
    if (!tag) return;
    const ctx = this.context();
    if (!ctx) return;
    const tags = [...(ctx.tags ?? [])];
    if (tags.includes(tag)) return;
    tags.push(tag);
    this.service.updateTags(this.name(), tags).subscribe({
      next: (updated) => this.context.set(updated),
      error: (err) => this.error.set(err.error?.detail || 'Failed to add tag'),
    });
  }

  removeTag(tag: string) {
    const ctx = this.context();
    if (!ctx) return;
    const tags = (ctx.tags ?? []).filter(t => t !== tag);
    this.service.updateTags(this.name(), tags).subscribe({
      next: (updated) => this.context.set(updated),
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove tag'),
    });
  }

  onTagKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') { event.preventDefault(); this.addTag(); }
    else if (event.key === 'Escape') { this.newTag.set(''); this.addingTag.set(false); }
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
        this.editingChildName.set(false);
        if (this.child()) {
          // On child route — navigate to updated URL so route params refresh
          this.router.navigate(['/contexts', parentName, newName], { replaceUrl: true });
        } else {
          // On parent route with accordion — just update local state
          this.expandedChild.set(newName);
          this.reloadContext();
          this.loadChildPreview(parentName, newName);
        }
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

  // ── Child description editing ──

  startEditChildDescription(childName: string) {
    if (!this.editing()) return;
    const ctx = this.context();
    if (!ctx) return;
    const child = ctx.children?.find(c => c.name === childName);
    this.editChildDescriptionValue.set(child?.description || '');
    this.editingChildDescription.set(true);
    this.expandedChild.set(childName);
  }

  saveChildDescription() {
    const parentName = this.name();
    const childName = this.expandedChild() || this.child();
    if (!childName) { this.editingChildDescription.set(false); return; }
    const desc = this.editChildDescriptionValue().trim();
    const ctx = this.context();
    const child = ctx?.children?.find(c => c.name === childName);
    if (!child || desc === (child.description || '')) {
      this.editingChildDescription.set(false);
      return;
    }
    this.service.updateChildDescription(parentName, childName, desc).subscribe({
      next: (updated) => {
        this.context.set(updated);
        this.editingChildDescription.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to update description');
        this.editingChildDescription.set(false);
      },
    });
  }

  cancelEditChildDescription() { this.editingChildDescription.set(false); }

  onChildDescriptionKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.saveChildDescription();
    else if (event.key === 'Escape') this.cancelEditChildDescription();
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

  copiedRef = signal(false);

  copyContextRef() {
    const path = this.child() ? `${this.name()}/${this.child()}` : this.name();
    const text = `Use roadmap MCP for context "${path}":\n`
      + `1. get_context_toc("${path}") — table of contents with item sizes\n`
      + `2. get_context_item("${path}", <index>) — fetch individual items\n`
      + `3. get_context("${path}") — fetch everything (can be large)`;
    navigator.clipboard.writeText(text).then(() => {
      this.copiedRef.set(true);
      setTimeout(() => this.copiedRef.set(false), 1500);
    });
  }

  dismissError() { this.error.set(''); }

  getItemIcon = getItemIcon;
  getItemTypeLabel = getItemTypeLabel;
  getItemDisplayId = getItemDisplayId;
}
