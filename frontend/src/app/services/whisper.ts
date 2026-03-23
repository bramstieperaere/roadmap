import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, map } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class WhisperService {
  private http = inject(HttpClient);

  status(): Observable<boolean> {
    return this.http.get<{ configured: boolean }>('/api/whisper/status')
      .pipe(map(r => r.configured));
  }

  transcribe(blob: Blob, filename = 'audio.webm'): Observable<string> {
    const form = new FormData();
    form.append('file', blob, filename);
    return this.http.post<{ text: string }>('/api/whisper/transcribe', form)
      .pipe(map(r => r.text));
  }
}
