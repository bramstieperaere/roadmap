import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ContextsService, ContextItem } from '../services/contexts';

@Component({
  selector: 'app-contexts',
  imports: [FormsModule, RouterLink],
  templateUrl: './contexts.html',
  styleUrl: './contexts.scss',
})
export class ContextsComponent implements OnInit {
  private service = inject(ContextsService);

  contexts = signal<ContextItem[]>([]);
  loading = signal(false);
  newName = signal('');
  adding = signal(false);
  error = signal('');

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

  addContext() {
    const name = this.newName().trim();
    if (!name) return;
    this.adding.set(true);
    this.error.set('');
    this.service.add(name).subscribe({
      next: () => {
        this.newName.set('');
        this.adding.set(false);
        this.load();
      },
      error: (err) => {
        this.adding.set(false);
        this.error.set(err.error?.detail || 'Failed to add context');
      },
    });
  }

  onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') this.addContext();
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

  dismissError() {
    this.error.set('');
  }
}
