import { Component, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { EncryptionService } from '../services/encryption';

@Component({
  selector: 'app-unlock-popup',
  imports: [FormsModule],
  templateUrl: './unlock-popup.html',
  styleUrl: './unlock-popup.scss',
})
export class UnlockPopup {
  private encryptionService = inject(EncryptionService);

  isFirstTime = input(false);
  unlocked = output<void>();

  password = signal('');
  error = signal<string | null>(null);
  submitting = signal(false);

  submit() {
    const pwd = this.password().trim();
    if (!pwd) {
      this.error.set('Please enter a password.');
      return;
    }
    this.error.set(null);
    this.submitting.set(true);
    this.encryptionService.unlock(pwd).subscribe({
      next: () => {
        this.submitting.set(false);
        this.unlocked.emit();
      },
      error: (err) => {
        this.submitting.set(false);
        this.error.set(err.error?.detail || 'Unlock failed');
      },
    });
  }
}
