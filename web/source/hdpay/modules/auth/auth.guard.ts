import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';

import { AuthService } from '../../services/auth/auth.service';

/**
 * Signed-in users only. After a page reload the user is loaded first (the interceptor refreshes an
 * expired token on the way); a session that cannot be restored goes to the sign-in page.
 */
export const adminGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);
  if (!authService.isSignedIn()) {
    return router.createUrlTree(['/login']);
  }
  return authService.me().pipe(
    map(() => true),
    catchError(() => {
      authService.clearSession();
      return of(router.createUrlTree(['/login']));
    })
  );
};

/** Owners only (the sweeper and users pages); anyone else goes to the overview. */
export const ownerGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);
  const allow = () => (authService.isOwner() ? true : router.createUrlTree(['/dashboard']));
  return authService.user() ? allow() : authService.me().pipe(map(allow), catchError(() => of(router.createUrlTree(['/login']))));
};

/** Signed-out visitors only; a signed-in one goes to the dashboard. */
export const publicGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  return authService.isSignedIn() ? inject(Router).createUrlTree(['/dashboard']) : true;
};
