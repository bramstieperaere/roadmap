import { Injectable, signal } from '@angular/core';
import { ConfluenceSpace, ConfluencePageSummary } from '../services/confluence';

@Injectable({ providedIn: 'root' })
export class ConfluenceStateService {
  spaces = signal<ConfluenceSpace[]>([]);
  loadingSpaces = signal(true);
  selectedSpaceKey = signal<string>('');
  pageTree = signal<ConfluencePageSummary[]>([]);
  loadingPages = signal(false);
  expandedNodes = signal<Set<string>>(new Set());
}
