import { Component, inject, OnInit, signal, ViewChild } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Header } from './header/header';
import { UnlockPopup } from './unlock-popup/unlock-popup';
import { EncryptionService } from './services/encryption';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, Header, UnlockPopup],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private encryptionService = inject(EncryptionService);

  @ViewChild(Header) header!: Header;

  showUnlockPopup = signal(false);
  isFirstTime = signal(false);

  ngOnInit() {
    this.encryptionService.getStatus().subscribe({
      next: (status) => {
        if (status.locked) {
          this.isFirstTime.set(!status.has_encrypted_fields);
          this.showUnlockPopup.set(true);
        }
      },
    });
  }

  onUnlocked() {
    this.showUnlockPopup.set(false);
    this.header.checkLockStatus();
    this.encryptionService.emitUnlocked();
  }
}
