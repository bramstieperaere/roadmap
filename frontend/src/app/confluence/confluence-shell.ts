import { Component, computed, HostListener, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NgTemplateOutlet } from '@angular/common';
import { ConfluenceService, ConfluencePageSummary } from '../services/confluence';
import { FunctionalService } from '../services/functional';
import { ConfluenceStateService } from './confluence-state';

@Component({
  selector: 'app-confluence-spaces',
  imports: [RouterLink, NgTemplateOutlet],
  templateUrl: './confluence-shell.html',
  styleUrl: './confluence-shell.scss',
})
export class ConfluenceSpacesComponent implements OnInit {
  private confluenceService = inject(ConfluenceService);
  private functionalService = inject(FunctionalService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  state = inject(ConfluenceStateService);
  refreshing = signal(false);
  refreshMessage = signal('');
  processing = signal(false);
  searchFilter = signal('');

  // Context menu
  contextMenu = signal<{ x: number; y: number; page: ConfluencePageSummary } | null>(null);

  filteredTree = computed(() => {
    const query = this.searchFilter().toLowerCase().trim();
    const pages = this.state.pageTree();
    if (!query) return pages;
    return this._filterTree(pages, query);
  });

  private _filterTree(pages: ConfluencePageSummary[], query: string): ConfluencePageSummary[] {
    const result: ConfluencePageSummary[] = [];
    for (const page of pages) {
      const titleMatch = page.title.toLowerCase().includes(query);
      const filteredChildren = this._filterTree(page.children, query);
      if (titleMatch || filteredChildren.length > 0) {
        result.push({ ...page, children: filteredChildren });
      }
    }
    return result;
  }

  ngOnInit() {
    const routeSpaceKey = this.route.snapshot.paramMap.get('spaceKey');

    this.confluenceService.getSpaces().subscribe({
      next: (spaces) => {
        this.state.spaces.set(spaces);
        this.state.loadingSpaces.set(false);

        if (routeSpaceKey && spaces.some(s => s.key === routeSpaceKey)) {
          // URL has a space key -- select it (only load pages if different from current)
          if (this.state.selectedSpaceKey() !== routeSpaceKey || this.state.pageTree().length === 0) {
            this._loadSpace(routeSpaceKey);
          }
        } else if (spaces.length > 0 && !this.state.selectedSpaceKey()) {
          this.selectSpace(spaces[0].key);
        }
      },
      error: () => this.state.loadingSpaces.set(false),
    });
  }

  selectSpace(key: string) {
    this.router.navigate(['/confluence', key], { replaceUrl: this.state.selectedSpaceKey() === '' });
    this._loadSpace(key);
  }

  private _loadSpace(key: string) {
    this.state.selectedSpaceKey.set(key);
    this.state.loadingPages.set(true);
    this.state.expandedNodes.set(new Set());
    this.searchFilter.set('');
    this.confluenceService.getPages(key).subscribe({
      next: (data) => {
        this.state.pageTree.set(data.pages);
        this.state.loadingPages.set(false);
      },
      error: () => this.state.loadingPages.set(false),
    });
  }

  toggleNode(id: string) {
    this.state.expandedNodes.update(set => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  isExpanded(id: string): boolean {
    return this.state.expandedNodes().has(id);
  }

  openPage(pageId: string) {
    this.router.navigate(['/confluence', this.state.selectedSpaceKey(), 'page', pageId]);
  }

  processSpace() {
    const key = this.state.selectedSpaceKey();
    if (!key) return;
    this.processing.set(true);
    this.functionalService.processPages(key).subscribe({
      next: (res) => {
        this.processing.set(false);
        this.router.navigate(['/jobs', res.job_id]);
      },
      error: (err) => {
        this.processing.set(false);
        this.refreshMessage.set(err.error?.detail || 'Processing failed');
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
    });
  }

  viewProcessed() {
    const key = this.state.selectedSpaceKey();
    if (key) this.router.navigate(['/functional', key]);
  }

  refreshPageTree() {
    const key = this.state.selectedSpaceKey();
    if (!key) return;
    this.state.loadingPages.set(true);
    this.confluenceService.getPages(key, true).subscribe({
      next: (data) => {
        this.state.pageTree.set(data.pages);
        this.state.loadingPages.set(false);
        this.refreshMessage.set(`Page tree refreshed (${data.total} pages)`);
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
      error: (err) => {
        this.state.loadingPages.set(false);
        this.refreshMessage.set(err.error?.detail || 'Refresh failed');
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
    });
  }

  @HostListener('document:click')
  onDocumentClick() {
    this.contextMenu.set(null);
  }

  onTreeContext(event: MouseEvent, page: ConfluencePageSummary) {
    event.preventDefault();
    this.contextMenu.set({ x: event.clientX, y: event.clientY, page });
  }

  refreshSubtree() {
    const ctx = this.contextMenu();
    const key = this.state.selectedSpaceKey();
    if (!ctx || !key) return;
    this.contextMenu.set(null);

    const ids = this._collectIds(ctx.page);
    this.refreshing.set(true);
    this.refreshMessage.set(`Refreshing ${ids.length} page(s)...`);
    this.confluenceService.refreshPages(key, ids).subscribe({
      next: (result) => {
        this.refreshing.set(false);
        const msg = `Refreshed ${result.pages_refreshed}/${result.pages_total} pages` +
          (result.errors.length ? ` (${result.errors.length} errors)` : '');
        this.refreshMessage.set(msg);
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
      error: (err) => {
        this.refreshing.set(false);
        this.refreshMessage.set(err.error?.detail || 'Refresh failed');
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
    });
  }

  private _collectIds(page: ConfluencePageSummary): string[] {
    const ids = [page.id];
    for (const child of page.children) {
      ids.push(...this._collectIds(child));
    }
    return ids;
  }

  refreshAllPages() {
    const key = this.state.selectedSpaceKey();
    if (!key) return;
    this.refreshing.set(true);
    this.refreshMessage.set('');
    this.confluenceService.refreshSpace(key).subscribe({
      next: (result) => {
        this.refreshing.set(false);
        const msg = `Refreshed ${result.pages_refreshed}/${result.pages_total} pages` +
          (result.errors.length ? ` (${result.errors.length} errors)` : '');
        this.refreshMessage.set(msg);
        setTimeout(() => this.refreshMessage.set(''), 5000);
        this.selectSpace(key);
      },
      error: (err) => {
        this.refreshing.set(false);
        this.refreshMessage.set(err.error?.detail || 'Refresh failed');
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
    });
  }
}
