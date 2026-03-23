import { Component, ElementRef, input, model, OnInit, output, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { WhisperRecording } from './whisper-recording';

@Component({
  selector: 'app-whisper-input',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="whisper-input-wrap">
      <input #inputEl
             type="text"
             [class]="inputClass()"
             [placeholder]="placeholder()"
             [ngModel]="value()" (ngModelChange)="value.set($event)"
             [disabled]="disabled() || transcribing()"
             (keydown)="onKeydown($event)">
      @if (whisperConfigured()) {
        <button class="btn-mic" [class.recording]="recording()" [class.transcribing]="transcribing()"
                (click)="toggleRecording()" [disabled]="disabled() || transcribing()"
                [title]="recording() ? 'Stop recording' : transcribing() ? 'Transcribing...' : 'Record voice'"
                type="button">
          @if (transcribing()) {
            <span class="spinner-border spinner-border-sm"></span>
          } @else {
            <i class="bi" [class.bi-mic-fill]="recording()" [class.bi-mic]="!recording()"></i>
          }
        </button>
      }
    </div>
    @if (error()) {
      <div class="text-danger small mt-1"><i class="bi bi-exclamation-triangle me-1"></i>{{ error() }}</div>
    }
  `,
  styleUrl: './whisper-input.scss',
})
export class WhisperInput extends WhisperRecording implements OnInit {
  value = model('');
  placeholder = input('');
  disabled = input(false);
  inputClass = input('form-control');

  /** Emitted when Enter is pressed (not during recording/transcribing). */
  submit = output<void>();

  @ViewChild('inputEl') private inputEl?: ElementRef<HTMLInputElement>;

  ngOnInit() {
    this.init(
      () => this.inputEl?.nativeElement.selectionStart ?? this.value().length,
      (text, caretPos) => {
        const current = this.value();
        const before = current.slice(0, caretPos);
        const after = current.slice(caretPos);
        const sep = before && !before.endsWith(' ') && !before.endsWith('\n') ? ' ' : '';
        this.value.set(before + sep + text + after);
      },
    );
  }

  onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.submit.emit();
    }
  }
}
