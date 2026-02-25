import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors, HttpRequest, HttpHandlerFn } from '@angular/common/http';

import { routes } from './app.routes';

function noCacheInterceptor(req: HttpRequest<unknown>, next: HttpHandlerFn) {
  if (req.url.startsWith('/api/')) {
    const noCacheReq = req.clone({
      setHeaders: {
        'Cache-Control': 'no-cache, no-store',
        'Pragma': 'no-cache',
      },
    });
    return next(noCacheReq);
  }
  return next(req);
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([noCacheInterceptor])),
  ]
};
