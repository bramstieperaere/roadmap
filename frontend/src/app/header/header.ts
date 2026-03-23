import { Component, computed, inject, signal, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { EncryptionService } from '../services/encryption';

@Component({
  selector: 'app-header',
  imports: [RouterLink, RouterLinkActive],
  templateUrl: './header.html',
  styleUrl: './header.scss',
})
export class Header implements OnInit {
  private encryptionService = inject(EncryptionService);
  private http = inject(HttpClient);
  private router = inject(Router);

  private currentUrl = toSignal(this.router.events, { initialValue: null });

  sourcesActive = computed(() => {
    this.currentUrl();
    const url = this.router.url;
    return url.startsWith('/sources') || url.startsWith('/jira') || url.startsWith('/confluence');
  });

  diagramsActive = computed(() => {
    this.currentUrl();
    const url = this.router.url;
    return url.startsWith('/diagrams') || url.startsWith('/query') || url.startsWith('/sequence')
      || url.startsWith('/data-flow') || url.startsWith('/functional');
  });

  processingActive = computed(() => {
    this.currentUrl();
    const url = this.router.url;
    return url.startsWith('/processing') || url.startsWith('/git-mining') || url.startsWith('/jobs');
  });

  isLocked = signal(true);
  backendStarted = signal('');

  ngOnInit() {
    this.checkLockStatus();
    this.http.get<{ started_at: string }>('/api/info').subscribe({
      next: (info) => {
        const d = new Date(info.started_at);
        this.backendStarted.set(d.toLocaleTimeString());
      },
    });
  }

  checkLockStatus() {
    this.encryptionService.getStatus().subscribe({
      next: (status) => this.isLocked.set(status.locked),
      error: () => this.isLocked.set(true),
    });
  }
}
