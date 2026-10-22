import { DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { filter, fromEvent, interval, merge } from 'rxjs';

/** How often an open page reloads its data in the background. */
export const LIVE_MS = 5000;

/**
 * Keeps a page current without a Refresh button: calls `refresh` every few seconds while the tab is
 * visible, and as soon as a hidden tab is shown again, so changes made elsewhere (the watchers, the
 * sweeper, the projects' backends, other admins) show up by themselves. A hidden tab asks for nothing.
 */
export function live(refresh: () => void, destroyRef: DestroyRef, ms = LIVE_MS): void {
  const visible = () => document.visibilityState === 'visible';
  merge(interval(ms).pipe(filter(visible)), fromEvent(document, 'visibilitychange').pipe(filter(visible)))
    .pipe(takeUntilDestroyed(destroyRef))
    .subscribe(() => refresh());
}
