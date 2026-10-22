import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Title } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzEmptyModule } from 'ng-zorro-antd/empty';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzStatisticModule } from 'ng-zorro-antd/statistic';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';
import { catchError, of, switchMap, tap, timer } from 'rxjs';

import { formatUnits } from '../../../modules/amount';
import { errorMessage } from '../../../modules/error';
import { DepositInterface, SweepStage, SweeperInterface, SweeperStateInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';

/** A sweeper makes a pass every 30 seconds; two minutes of silence means it stopped. */
const STALE_MINUTES = 2;
const REFRESH_MS = 10_000;

export const STAGE: Record<SweepStage, { label: string; color: string; hint: string }> = {
  waiting: { label: 'waiting', color: 'blue', hint: 'Credited; the sweeper picks it up on its next pass' },
  topping_up: { label: 'topping up gas', color: 'orange', hint: 'The fee funder sent gas or TRX; waiting for it to land' },
  sweeping: { label: 'sweeping', color: 'processing', hint: 'Sweep sent; waiting for it to confirm' },
  held: { label: 'held', color: 'red', hint: 'A transaction failed on chain; the sweeper skips it until released' }
};

@Component({
  selector: 'hdpay-sweeper',
  imports: [
    DatePipe,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzEmptyModule,
    NzIconModule,
    NzPopconfirmModule,
    NzSkeletonModule,
    NzStatisticModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule
  ],
  templateUrl: './sweeper.component.html',
  styleUrl: './sweeper.component.css'
})
export class SweeperComponent {
  private readonly adminService = inject(AdminService);
  private readonly message = inject(NzMessageService);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly data = signal<SweeperInterface | null>(null);
  protected readonly loading = signal(true);   // until the first answer
  protected readonly error = signal<string | null>(null);
  protected readonly updatedAt = signal<Date | null>(null);
  protected readonly releasing = signal<string | null>(null);
  protected readonly stages = Object.keys(STAGE) as SweepStage[];
  protected readonly stage = STAGE;
  protected readonly formatUnits = formatUnits;

  constructor() {
    this.title.setTitle('HDPay Admin - Sweeper');
    // Sweeps move every few seconds, so the page keeps itself current.
    timer(0, REFRESH_MS)
      .pipe(
        switchMap(() => this.fetch()),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe();
  }

  /** After a release: show it at once rather than at the next poll. */
  private refresh(): void {
    this.fetch().subscribe();
  }

  private fetch() {
    return this.adminService.sweeper().pipe(
      tap((data) => {
        this.data.set(data);
        this.updatedAt.set(new Date());
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

  protected stageOf(d: DepositInterface): SweepStage {
    if (d.sweep_hold) {
      return 'held';
    }
    if (d.sweep_tx_hash) {
      return 'sweeping';
    }
    return d.fee_tx_hash ? 'topping_up' : 'waiting';
  }

  /** Minutes since the sweeper's last pass; null if it never ran. */
  protected minutesSince(sweeper: SweeperStateInterface): number | null {
    if (!sweeper.heartbeat_at) {
      return null;
    }
    return Math.floor((Date.now() - new Date(sweeper.heartbeat_at).getTime()) / 60000);
  }

  protected state(sweeper: SweeperStateInterface): 'not started' | 'running' | 'stalled' {
    const minutes = this.minutesSince(sweeper);
    if (minutes === null) {
      return 'not started';
    }
    return minutes >= STALE_MINUTES ? 'stalled' : 'running';
  }

  protected stalledCount(list: SweeperStateInterface[]): number {
    return list.filter((s) => this.state(s) === 'stalled').length;
  }

  protected short(value: string): string {
    return `${value.slice(0, 8)}…${value.slice(-6)}`;
  }

  protected release(d: DepositInterface): void {
    this.releasing.set(d.id);
    this.adminService.release(d.id).subscribe({
      next: () => {
        this.releasing.set(null);
        this.message.success('Released. The sweeper tries again on its next pass.');
        this.refresh();
      },
      error: (response: HttpErrorResponse) => {
        this.releasing.set(null);
        this.message.error(errorMessage(response));
      }
    });
  }
}
