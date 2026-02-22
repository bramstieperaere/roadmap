import {
  Component, inject, signal, output, OnDestroy, ElementRef, ViewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, switchMap, of, takeUntil } from 'rxjs';
import { BrowseService, SearchResult } from '../../services/browse';

const ICON_MAP: Record<string, string> = {
  Repository: 'bi-archive',
  Module: 'bi-box',
  Package: 'bi-folder',
  Class: 'bi-file-earmark-code',
  Method: 'bi-gear',
  Microservice: 'bi-diagram-3',
  RESTInterface: 'bi-globe',
  RESTEndpoint: 'bi-link-45deg',
  FeignClient: 'bi-cloud-arrow-up',
  FeignEndpoint: 'bi-cloud',
  JMSDestination: 'bi-mailbox',
  JMSListener: 'bi-inbox',
  JMSProducer: 'bi-send',
  ScheduledTask: 'bi-clock',
  HTTPClient: 'bi-arrow-left-right',
};

function getIcon(labels: string[]): string {
  for (const label of labels) {
    if (ICON_MAP[label]) return ICON_MAP[label];
  }
  return 'bi-circle';
}

function getTypeLabel(labels: string[]): string {
  const skip = new Set(['Java', 'Arch']);
  for (const l of labels) {
    if (!skip.has(l)) return l;
  }
  return labels[0] || '';
}

@Component({
  selector: 'app-node-search',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './node-search.html',
  styleUrl: './node-search.scss',
})
export class NodeSearch implements OnDestroy {
  nodeSelected = output<SearchResult>();

  query = signal('');
  results = signal<SearchResult[]>([]);
  open = signal(false);
  activeIndex = signal(-1);

  private browseService = inject(BrowseService);
  private searchSubject = new Subject<string>();
  private destroy$ = new Subject<void>();

  @ViewChild('searchInput') searchInput!: ElementRef<HTMLInputElement>;

  constructor() {
    this.searchSubject.pipe(
      debounceTime(300),
      switchMap(q => q.length >= 2
        ? this.browseService.searchNodes(q)
        : of([])),
      takeUntil(this.destroy$),
    ).subscribe(results => {
      this.results.set(results);
      this.open.set(results.length > 0);
      this.activeIndex.set(-1);
    });
  }

  ngOnDestroy() {
    this.destroy$.next();
    this.destroy$.complete();
  }

  onInput(value: string) {
    this.query.set(value);
    this.searchSubject.next(value);
  }

  onKeydown(event: KeyboardEvent) {
    const results = this.results();
    if (!this.open() || results.length === 0) {
      if (event.key === 'Escape') {
        this.close();
      }
      return;
    }

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        this.activeIndex.update(i =>
          i < results.length - 1 ? i + 1 : 0);
        break;
      case 'ArrowUp':
        event.preventDefault();
        this.activeIndex.update(i =>
          i > 0 ? i - 1 : results.length - 1);
        break;
      case 'Enter':
        event.preventDefault();
        if (this.activeIndex() >= 0) {
          this.select(results[this.activeIndex()]);
        }
        break;
      case 'Escape':
        this.close();
        break;
    }
  }

  select(result: SearchResult) {
    this.nodeSelected.emit(result);
    this.close();
    this.query.set('');
  }

  close() {
    this.open.set(false);
    this.activeIndex.set(-1);
  }

  onBlur() {
    // Delay to allow click on result item
    setTimeout(() => this.close(), 200);
  }

  getIcon(labels: string[]): string {
    return getIcon(labels);
  }

  getTypeLabel(labels: string[]): string {
    return getTypeLabel(labels);
  }
}
