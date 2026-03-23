import { Component, computed, HostListener, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ContextsService, ContextItem } from '../services/contexts';
import { ConfirmDialogService } from '../components/confirm-dialog/confirm-dialog.service';

@Component({
  selector: 'app-contexts',
  imports: [FormsModule, RouterLink],
  templateUrl: './contexts.html',
  styleUrl: './contexts.scss',
})
export class ContextsComponent implements OnInit {
  private service = inject(ContextsService);
  private confirm = inject(ConfirmDialogService);
  private router = inject(Router);

  contexts = signal<ContextItem[]>([]);
  loading = signal(false);
  error = signal('');

  // Search filter (stored in service to survive navigation)
  searchQuery = this.service.searchQuery;
  hideDone = this.service.hideDone;
  collapsed = this.service.collapsed;
  tagFilter = this.service.tagFilter;

  allTags = computed(() => {
    const tags = new Set<string>();
    for (const ctx of this.contexts()) {
      for (const tag of ctx.tags ?? []) tags.add(tag);
    }
    return [...tags].sort();
  });

  filteredContexts = computed(() => {
    const q = this.searchQuery().toLowerCase().trim();
    const hide = this.hideDone();
    const activeTags = this.tagFilter();
    const all = this.contexts();
    return all.flatMap(ctx => {
      if (hide && ctx.done) return [];
      if (activeTags.length > 0) {
        const ctxTags = ctx.tags ?? [];
        if (!activeTags.some(t => ctxTags.includes(t))) return [];
      }
      let children = ctx.children;
      if (hide) children = children.filter(c => !c.done);
      if (q) {
        const parentMatch = ctx.name.toLowerCase().includes(q)
          || (ctx.description || '').toLowerCase().includes(q);
        if (!parentMatch) {
          children = children.filter(c => c.name.toLowerCase().includes(q));
          if (!children.length) return [];
        }
      }
      return children !== ctx.children ? [{ ...ctx, children }] : [ctx];
    });
  });

  toggleTagFilter(tag: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const current = this.tagFilter();
    this.tagFilter.set(
      current.includes(tag) ? current.filter(t => t !== tag) : [...current, tag]
    );
  }

  clearTagFilter() {
    this.tagFilter.set([]);
  }

  // Dropdown menu
  openMenu = signal<string | null>(null);
  menuPosition = signal<{ top: number; left: number }>({ top: 0, left: 0 });

  toggleMenu(key: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    if (this.openMenu() === key) {
      this.openMenu.set(null);
      return;
    }
    const btn = event.currentTarget as HTMLElement;
    const rect = btn.getBoundingClientRect();
    this.menuPosition.set({ top: rect.bottom + 2, left: rect.right });
    this.openMenu.set(key);
  }

  @HostListener('document:click')
  closeMenu() {
    this.openMenu.set(null);
  }

  // Add context popup
  showAddPopup = signal(false);
  newName = signal('');
  adding = signal(false);

  // Add sub-context
  addingChildFor = signal<string | null>(null);
  newChildName = signal('');
  addingChild = signal(false);

  ngOnInit() {
    this.load();
  }

  private load() {
    this.loading.set(true);
    this.service.getAll().subscribe({
      next: (items) => {
        this.contexts.set(items);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  openAddPopup() {
    this.newName.set('');
    this.showAddPopup.set(true);
  }

  cancelAddPopup() {
    this.showAddPopup.set(false);
  }

  addContext() {
    const name = this.newName().trim();
    if (!name) return;
    this.adding.set(true);
    this.error.set('');
    this.service.add(name).subscribe({
      next: (ctx) => {
        this.showAddPopup.set(false);
        this.router.navigate(['/contexts', ctx.name]);
      },
      error: (err) => {
        this.adding.set(false);
        this.error.set(err.error?.detail || 'Failed to add context');
      },
    });
  }

  onAddKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addContext();
    else if (event.key === 'Escape') this.cancelAddPopup();
  }

  showAddChild(parentName: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    this.addingChildFor.set(parentName);
    this.newChildName.set('');
  }

  cancelAddChild() {
    this.addingChildFor.set(null);
    this.newChildName.set('');
  }

  addChild() {
    const parentName = this.addingChildFor();
    const childName = this.newChildName().trim();
    if (!parentName || !childName) return;
    this.addingChild.set(true);
    this.error.set('');
    this.service.addChild(parentName, childName).subscribe({
      next: () => {
        this.addingChild.set(false);
        this.addingChildFor.set(null);
        this.newChildName.set('');
        this.load();
      },
      error: (err) => {
        this.addingChild.set(false);
        this.error.set(err.error?.detail || 'Failed to add sub-context');
      },
    });
  }

  onChildKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addChild();
    else if (event.key === 'Escape') this.cancelAddChild();
  }

  copyContextRef(contextPath: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const text = `Use roadmap MCP for context "${contextPath}":\n`
      + `1. get_context_toc("${contextPath}") — table of contents with item sizes\n`
      + `2. get_context_item("${contextPath}", <index>) — fetch individual items\n`
      + `3. get_context("${contextPath}") — fetch everything (can be large)\n`
      + `4. add_context_insight("${contextPath}", "<label>", "<text>") — write back your analysis/findings`;
    navigator.clipboard.writeText(text);
  }

  cloneContext(name: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const cloneName = prompt(`Clone "${name}" as:`);
    if (!cloneName?.trim()) return;
    this.error.set('');
    this.service.clone(name, cloneName.trim()).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to clone context'),
    });
  }

  cloneChild(parentName: string, childName: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const cloneName = prompt(`Clone "${childName}" as:`);
    if (!cloneName?.trim()) return;
    this.error.set('');
    this.service.cloneChild(parentName, childName, cloneName.trim()).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to clone sub-context'),
    });
  }

  toggleDone(name: string, currentDone: boolean, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    this.service.setDone(name, !currentDone).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to update'),
    });
  }

  toggleChildDone(parentName: string, childName: string, currentDone: boolean, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    this.service.setChildDone(parentName, childName, !currentDone).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to update'),
    });
  }

  async removeChild(parentName: string, childName: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const ok = await this.confirm.open({
      title: 'Remove subcontext',
      message: `Remove subcontext "${childName}" from "${parentName}"? This cannot be undone.`,
      confirmLabel: 'Remove',
      confirmClass: 'btn-danger',
    });
    if (!ok) return;
    this.service.removeChild(parentName, childName, true).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove subcontext'),
    });
  }

  async removeContext(name: string, event: Event) {
    event.preventDefault();
    event.stopPropagation();
    const ok = await this.confirm.open({
      title: 'Remove context',
      message: `Remove context "${name}"? This cannot be undone.`,
      confirmLabel: 'Remove',
      confirmClass: 'btn-danger',
    });
    if (!ok) return;
    this.service.remove(name, true).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(err.error?.detail || 'Failed to remove context'),
    });
  }

  dismissError() {
    this.error.set('');
  }
}
