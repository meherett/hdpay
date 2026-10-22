import { HttpErrorResponse, HttpEvent, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, switchMap, throwError } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth/auth.service';

/** Sign-in calls that carry no access token of their own. */
const PUBLIC_AUTH = ['/auth/login', '/auth/refresh', '/auth/logout'];

function withToken(request: HttpRequest<unknown>, token: string | null): HttpRequest<unknown> {
  return token ? request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : request;
}

/**
 * Adds the access token to calls to the API's /admin and /auth routes. On a 401 the token has
 * expired: refresh it once and retry. If the refresh fails too, the session is over: sign out.
 */
export const authInterceptor: HttpInterceptorFn = (request, next): Observable<HttpEvent<unknown>> => {
  const authService = inject(AuthService);
  const router = inject(Router);

  const api = request.url.startsWith(`${environment.endpoint}/admin`) || request.url.startsWith(`${environment.endpoint}/auth`);
  if (!api || PUBLIC_AUTH.some((path) => request.url.startsWith(`${environment.endpoint}${path}`))) {
    return next(request);
  }

  return next(withToken(request, authService.getAccessToken())).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status !== 401 || !authService.isSignedIn()) {
        return throwError(() => error);
      }
      return authService.refresh().pipe(
        switchMap((token) => next(withToken(request, token))),
        catchError((refreshError: HttpErrorResponse) => {
          authService.clearSession();
          void router.navigate(['/login']);
          return throwError(() => refreshError);
        })
      );
    })
  );
};
