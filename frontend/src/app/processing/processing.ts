import { Component, inject, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { SettingsService, RepositoryConfig } from '../services/settings';
import { JobsService } from '../services/jobs';

type GitJobType = 'analysis' | 'enrichment' | 'data-flow';

interface GitJobModal {
  type: GitJobType;
  title: string;
}

@Component({
  selector: 'app-processing',
  imports: [RouterLink],
  templateUrl: './processing.html',
  styleUrl: './processing.scss',
})
export class Processing implements OnInit {
  private settingsService = inject(SettingsService);
  private jobsService = inject(JobsService);
  private router = inject(Router);

  repos = signal<RepositoryConfig[]>([]);
  activeModal = signal<GitJobModal | null>(null);
  selected = signal<Set<number>>(new Set());
  starting = signal(false);
  message = signal<{ text: string; type: 'success' | 'danger' } | null>(null);

  ngOnInit() {
    this.settingsService.getSettings().subscribe({
      next: (config) => this.repos.set(config.repositories),
    });
  }

  openModal(type: GitJobType, title: string) {
    this.selected.set(new Set());
    this.activeModal.set({ type, title });
  }

  closeModal() {
    this.activeModal.set(null);
  }

  isSelected(i: number): boolean {
    return this.selected().has(i);
  }

  toggle(i: number) {
    this.selected.update(s => {
      const n = new Set(s);
      n.has(i) ? n.delete(i) : n.add(i);
      return n;
    });
  }

  selectAll() {
    this.selected.set(new Set(this.repos().map((_, i) => i)));
  }

  selectNone() {
    this.selected.set(new Set());
  }

  get hasSelection(): boolean {
    return this.selected().size > 0;
  }

  private get selectedIndices(): number[] {
    return [...this.selected()].sort((a, b) => a - b);
  }

  confirmStart() {
    const modal = this.activeModal();
    if (!modal || !this.hasSelection) return;

    this.starting.set(true);
    const indices = this.selectedIndices;
    const request = modal.type === 'analysis'
      ? this.jobsService.startAnalysis(indices)
      : modal.type === 'enrichment'
        ? this.jobsService.startEnrichment(indices)
        : this.jobsService.startDataFlow(indices);

    request.subscribe({
      next: () => {
        this.starting.set(false);
        this.activeModal.set(null);
        this.router.navigate(['/jobs']);
      },
      error: (err) => {
        this.starting.set(false);
        this.showMessage(err.error?.detail || 'Failed to start job', 'danger');
      },
    });
  }

  private showMessage(text: string, type: 'success' | 'danger') {
    this.message.set({ text, type });
    setTimeout(() => this.message.set(null), 4000);
  }
}
