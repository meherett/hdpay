import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Title } from '@angular/platform-browser';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { catchError, of, switchMap, tap, timer } from 'rxjs';

import { errorMessage } from '../../../modules/error';
import { WatcherStateInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';

/** A watcher polls every few seconds; two minutes of silence means it stopped. */
const STALE_MINUTES = 2;
const REFRESH_MS = 10_000;

@Component({
  selector: 'hdpay-watcher',
  imports: [
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzSkeletonModule,
    NzTableModule,
    NzTagModule
  ],
  templateUrl: './watcher.component.html',
  styleUrl: './watcher.component.css'
})
export class WatcherComponent {
  private readonly adminService = inject(AdminService);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly watchers = signal<WatcherStateInterface[] | null>(null);
  protected readonly loading = signal(true);   // until the first answer
  protected readonly error = signal<string | null>(null);

  protected readonly stalled = computed(() => (this.watchers() ?? []).filter((w) => this.state(w) === 'stalled').length);

  constructor() {
    this.title.setTitle('HDPay Admin - Watchers');
    // Watcher state changes every few seconds, so the page keeps itself current.
    timer(0, REFRESH_MS)
      .pipe(
        switchMap(() => this.fetch()),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe();
  }

  private fetch() {
    return this.adminService.stats().pipe(
      tap((stats) => {
        this.watchers.set(stats.watchers);
        this.error.set(null);
        this.loading.set(false);
      }),
      catchError((response: HttpErrorResponse) => {
        this.error.set(errorMessage(response));
        this.loading.set(false);
        return of(null);
      })
    );
  }

  /** Minutes since the watcher last ran; null if it never has. */
  protected minutesSince(watcher: WatcherStateInterface): number | null {
    if (!watcher.heartbeat_at) {
      return null;
    }
    return Math.floor((Date.now() - new Date(watcher.heartbeat_at).getTime()) / 60000);
  }

  protected state(watcher: WatcherStateInterface): 'not started' | 'running' | 'stalled' {
    const minutes = this.minutesSince(watcher);
    if (minutes === null) {
      return 'not started';
    }
    return minutes >= STALE_MINUTES ? 'stalled' : 'running';
  }

  protected lag(watcher: WatcherStateInterface): number | null {
    return watcher.head !== null && watcher.last_block !== null ? watcher.head - watcher.last_block : null;
  }
}
