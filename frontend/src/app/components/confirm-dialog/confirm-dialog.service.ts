import { Injectable, signal } from '@angular/core';

export interface ConfirmDialogOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  confirmClass?: string; // Bootstrap btn class suffix, e.g. 'danger', 'primary'
}

@Injectable({ providedIn: 'root' })
export class ConfirmDialogService {
  state = signal<ConfirmDialogOptions | null>(null);
  private resolve?: (result: boolean) => void;

  open(options: ConfirmDialogOptions): Promise<boolean> {
    return new Promise<boolean>(resolve => {
      this.resolve = resolve;
      this.state.set(options);
    });
  }

  confirm() {
    this.resolve?.(true);
    this.state.set(null);
  }

  cancel() {
    this.resolve?.(false);
    this.state.set(null);
  }
}
