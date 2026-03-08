import { Component, inject } from '@angular/core';
import { ConfirmDialogService } from './confirm-dialog.service';

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  template: `
    @if (svc.state(); as s) {
      <div class="modal-backdrop fade show"></div>
      <div class="modal fade show d-block" tabindex="-1" (click)="onBackdrop($event)">
        <div class="modal-dialog modal-dialog-centered modal-sm">
          <div class="modal-content">
            <div class="modal-header py-2">
              <h6 class="modal-title">{{ s.title }}</h6>
              <button type="button" class="btn-close btn-close-sm" (click)="svc.cancel()"></button>
            </div>
            <div class="modal-body py-2">
              <p class="mb-0 small">{{ s.message }}</p>
            </div>
            <div class="modal-footer py-2">
              <button type="button" class="btn btn-sm btn-outline-secondary" (click)="svc.cancel()">Cancel</button>
              <button type="button" class="btn btn-sm" [class]="'btn-' + (s.confirmClass || 'danger')" (click)="svc.confirm()">
                {{ s.confirmLabel || 'Delete' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    }
  `,
})
export class ConfirmDialog {
  svc = inject(ConfirmDialogService);

  onBackdrop(event: MouseEvent) {
    if ((event.target as HTMLElement).classList.contains('modal')) {
      this.svc.cancel();
    }
  }
}
