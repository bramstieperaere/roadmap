import { Component, inject, signal, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { RouterLink, RouterLinkActive } from '@angular/router';
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
