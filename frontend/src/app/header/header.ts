import { Component, inject, signal, OnInit } from '@angular/core';
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

  isLocked = signal(true);

  ngOnInit() {
    this.checkLockStatus();
  }

  checkLockStatus() {
    this.encryptionService.getStatus().subscribe({
      next: (status) => this.isLocked.set(status.locked),
      error: () => this.isLocked.set(true),
    });
  }
}
