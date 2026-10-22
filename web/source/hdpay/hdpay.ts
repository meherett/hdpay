import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationStart,
  Router,
  RouterOutlet
} from '@angular/router';

@Component({
  imports: [RouterOutlet],
  selector: 'hdpay-root',
  styleUrl: './hdpay.css',
  template: `
    @if (navigating()) {
      <div class="loading-bar" role="progressbar" aria-label="Loading"></div>
    }
    <router-outlet />
  `
})
export class Hdpay {
  /**
   * True while a route resolves. Pages load lazily, so without this the
   * screen stays blank with no sign of progress while a page's code arrives.
   */
  protected readonly navigating = signal(false);

  constructor() {
    inject(Router)
      .events.pipe(takeUntilDestroyed())
      .subscribe((event) => {
        if (event instanceof NavigationStart) {
          this.navigating.set(true);
        } else if (
          event instanceof NavigationEnd ||
          event instanceof NavigationCancel ||
          event instanceof NavigationError
        ) {
          this.navigating.set(false);
        }
      });
  }
}
