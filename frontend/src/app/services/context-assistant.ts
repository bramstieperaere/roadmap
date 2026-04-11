import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ProposedAction {
  action: string;  // "add_item" | "add_tag" | "remove_tag" | "set_description"
  type?: string;
  id?: string;
  label?: string;
  tag?: string;
  description?: string;
  reason: string;
}

export interface AssistantResponse {
  reply: string;
  proposals: ProposedAction[];
}

@Injectable({ providedIn: 'root' })
export class ContextAssistantService {
  private http = inject(HttpClient);

  assist(message: string, contextName: string,
         existingItems: { type: string; id: string; label?: string }[],
         tags: string[] = [], description = ''): Observable<AssistantResponse> {
    return this.http.post<AssistantResponse>('/api/context-assistant', {
      message,
      context_name: contextName,
      existing_items: existingItems,
      tags,
      description,
    });
  }
}
