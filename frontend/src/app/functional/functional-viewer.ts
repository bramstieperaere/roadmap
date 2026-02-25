import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import {
  FunctionalService,
  FunctionalIndex,
  FunctionalIndexEntry,
  FunctionalDoc,
} from '../services/functional';
import { ConfluenceService, ConfluenceSpace } from '../services/confluence';

@Component({
  selector: 'app-functional-viewer',
  imports: [RouterLink, DatePipe],
  templateUrl: './functional-viewer.html',
  styleUrl: './functional-viewer.scss',
})
export class FunctionalViewerComponent implements OnInit {
  private functionalService = inject(FunctionalService);
  private confluenceService = inject(ConfluenceService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  spaceKey = signal('');
  pageId = signal<string | null>(null);

  // Space picker (when no spaceKey in route)
  spaces = signal<ConfluenceSpace[]>([]);
  loadingSpaces = signal(false);

  // Index view
  index = signal<FunctionalIndex | null>(null);
  loadingIndex = signal(false);
  indexError = signal('');

  // Detail view
  doc = signal<FunctionalDoc | null>(null);
  loadingDoc = signal(false);
  docError = signal('');

  ngOnInit() {
    this.route.paramMap.subscribe(params => {
      const sk = params.get('spaceKey') || '';
      const pid = params.get('pageId') || null;
      this.spaceKey.set(sk);
      this.pageId.set(pid);

      if (pid) {
        this.fetchDoc(sk, pid);
      } else if (sk) {
        this.fetchIndex(sk);
      } else {
        this.loadSpaces();
      }
    });
  }

  private fetchIndex(spaceKey: string) {
    this.loadingIndex.set(true);
    this.indexError.set('');
    this.functionalService.getIndex(spaceKey).subscribe({
      next: (data) => {
        this.index.set(data);
        this.loadingIndex.set(false);
      },
      error: (err) => {
        this.loadingIndex.set(false);
        this.indexError.set(err.error?.detail || 'Failed to load index');
      },
    });
  }

  private fetchDoc(spaceKey: string, pageId: string) {
    this.loadingDoc.set(true);
    this.docError.set('');
    this.functionalService.getDoc(spaceKey, pageId).subscribe({
      next: (data) => {
        this.doc.set(data);
        this.loadingDoc.set(false);
      },
      error: (err) => {
        this.loadingDoc.set(false);
        this.docError.set(err.error?.detail || 'Failed to load document');
      },
    });
  }

  private loadSpaces() {
    this.loadingSpaces.set(true);
    this.confluenceService.getSpaces().subscribe({
      next: (spaces) => {
        this.spaces.set(spaces);
        this.loadingSpaces.set(false);
      },
      error: () => this.loadingSpaces.set(false),
    });
  }

  selectSpace(key: string) {
    this.router.navigate(['/functional', key]);
  }

  openDoc(entry: FunctionalIndexEntry) {
    this.router.navigate(['/functional', this.spaceKey(), entry.page_id]);
  }

  goBackToIndex() {
    this.router.navigate(['/functional', this.spaceKey()]);
  }

  metadataEntries(doc: FunctionalDoc): [string, string][] {
    return Object.entries(doc.metadata || {});
  }

  refIcon(refType: string): string {
    switch (refType) {
      case 'jira_issue': return 'bi-bug';
      case 'confluence_page': return 'bi-journal-text';
      case 'external_system': return 'bi-box-arrow-up-right';
      case 'url': return 'bi-link-45deg';
      default: return 'bi-link';
    }
  }

  docTypeBadgeClass(docType: string): string {
    switch (docType) {
      case 'specification': return 'bg-primary';
      case 'process': return 'bg-info';
      case 'requirement': return 'bg-warning text-dark';
      case 'test': return 'bg-success';
      case 'decision': return 'bg-danger';
      case 'guide': return 'bg-secondary';
      case 'reference': return 'bg-dark';
      default: return 'bg-light text-dark';
    }
  }

  sectionTypeBadgeClass(sectionType: string): string {
    switch (sectionType) {
      case 'description': return 'bg-primary';
      case 'rule': return 'bg-warning text-dark';
      case 'mapping': return 'bg-info';
      case 'technical': return 'bg-dark';
      case 'test_case': return 'bg-success';
      case 'example': return 'bg-secondary';
      default: return 'bg-light text-dark';
    }
  }
}
