import { inject, signal } from '@angular/core';
import { WhisperService } from '../../services/whisper';

/**
 * Shared recording logic for whisper-powered text inputs.
 * Call init() in ngOnInit, and provide getCaretPos / insertText callbacks.
 */
export class WhisperRecording {
  private whisperService = inject(WhisperService);

  whisperConfigured = signal(false);
  recording = signal(false);
  transcribing = signal(false);
  error = signal('');

  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private getCaretPos: () => number = () => 0;
  private insertText: (text: string, caretPos: number) => void = () => {};

  init(getCaretPos: () => number, insertText: (text: string, caretPos: number) => void) {
    this.getCaretPos = getCaretPos;
    this.insertText = insertText;
    this.whisperService.status().subscribe({
      next: (ok) => this.whisperConfigured.set(ok),
      error: () => {},
    });
  }

  async toggleRecording() {
    if (this.recording()) {
      this.stopRecording();
    } else {
      await this.startRecording();
    }
  }

  private async startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.audioChunks = [];
      const mimeType = this.getSupportedMimeType();
      this.mediaRecorder = new MediaRecorder(stream, { mimeType });
      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.audioChunks.push(e.data);
      };
      this.mediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        this.sendToWhisper();
      };
      this.mediaRecorder.start();
      this.recording.set(true);
      this.error.set('');
    } catch {
      this.error.set('Microphone access denied');
    }
  }

  private stopRecording() {
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.stop();
    }
    this.recording.set(false);
  }

  private sendToWhisper() {
    const mimeType = this.mediaRecorder?.mimeType || 'audio/webm';
    const blob = new Blob(this.audioChunks, { type: mimeType });
    if (blob.size === 0) return;
    const caretPos = this.getCaretPos();
    this.transcribing.set(true);
    const ext = mimeType.includes('mp4') ? 'mp4' : 'webm';
    this.whisperService.transcribe(blob, `recording.${ext}`).subscribe({
      next: (text) => {
        this.transcribing.set(false);
        this.insertText(text, caretPos);
      },
      error: (err) => {
        this.transcribing.set(false);
        this.error.set(err.error?.detail || 'Transcription failed');
      },
    });
  }

  private getSupportedMimeType(): string {
    for (const mime of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    }
    return '';
  }
}
