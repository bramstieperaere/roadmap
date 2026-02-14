import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface LockStatus {
  locked: boolean;
  has_encrypted_fields: boolean;
}

@Injectable({
  providedIn: 'root',
})
export class EncryptionService {
  private http = inject(HttpClient);

  getStatus(): Observable<LockStatus> {
    return this.http.get<LockStatus>('/api/encryption/status');
  }

  unlock(password: string): Observable<{ status: string; message: string }> {
    return this.http.post<{ status: string; message: string }>('/api/encryption/unlock', { password });
  }

  lock(): Observable<{ status: string; message: string }> {
    return this.http.post<{ status: string; message: string }>('/api/encryption/lock', {});
  }
}
