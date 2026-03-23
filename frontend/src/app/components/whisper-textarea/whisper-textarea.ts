import { Component, ElementRef, input, model, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { WhisperRecording } from './whisper-recording';

@Component({
  selector: 'app-whisper-textarea',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './whisper-textarea.html',
  styleUrl: './whisper-textarea.scss',
})
export class WhisperTextarea extends WhisperRecording implements OnInit {
  value = model('');
  placeholder = input('');
  rows = input(4);
  disabled = input(false);
  textareaClass = input('form-control');

  @ViewChild('textarea') private textareaEl?: ElementRef<HTMLTextAreaElement>;

  ngOnInit() {
    this.init(
      () => this.textareaEl?.nativeElement.selectionStart ?? this.value().length,
      (text, caretPos) => {
        const current = this.value();
        const before = current.slice(0, caretPos);
        const after = current.slice(caretPos);
        const sep = before && !before.endsWith(' ') && !before.endsWith('\n') ? ' ' : '';
        this.value.set(before + sep + text + after);
      },
    );
  }
}
