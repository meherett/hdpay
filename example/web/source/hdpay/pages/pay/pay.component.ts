import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzQRCodeModule } from 'ng-zorro-antd/qr-code';
import { NzResultModule } from 'ng-zorro-antd/result';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzSpinModule } from 'ng-zorro-antd/spin';
import { NzStepsModule } from 'ng-zorro-antd/steps';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { Observable, catchError, interval, of, switchMap, takeWhile, tap, timer } from 'rxjs';

import { formatUnits } from '../../modules/amount';
import { errorMessage } from '../../modules/error';
import { DepositInterface } from '../../services/api.interface';
import { DepositService } from '../../services/deposit/deposit.service';

const POLL_MS = 5000;

@Component({
  selector: 'hdpay-pay',
  imports: [
    DatePipe,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzPopconfirmModule,
    NzQRCodeModule,
    NzResultModule,
    NzSkeletonModule,
    NzSpinModule,
    NzStepsModule,
    NzTagModule
  ],
  templateUrl: './pay.component.html',
  styleUrl: './pay.component.css'
})
export class PayComponent {
  private readonly depositService = inject(DepositService);
  private readonly message = inject(NzMessageService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);

  /** The deposit id, bound from the :id route parameter. */
  readonly id = input.required<string>();

  protected readonly deposit = signal<DepositInterface | null>(null);
  protected readonly notFound = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly renewing = signal(false);
  protected readonly cancelling = signal(false);
  protected readonly reopening = signal(false);
  private readonly now = signal(Date.now());

  protected readonly secondsLeft = computed(() => {
    const deposit = this.deposit();
    if (!deposit) {
      return 0;
    }
    return Math.max(0, Math.floor((new Date(deposit.expires_at).getTime() - this.now()) / 1000));
  });

  protected readonly countdown = computed(() => {
    const seconds = this.secondsLeft();
    const pad = (value: number) => String(value).padStart(2, '0');
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    // a remainder window can be a day long
    return hours ? `${hours}:${pad(minutes)}:${pad(seconds % 60)}` : `${minutes}:${pad(seconds % 60)}`;
  });

  /**
   * The server closes a window on its next watcher pass; the page shows it as soon as the clock
   * runs out: pending becomes expired, and a partial payment goes to support.
   */
  protected readonly view = computed(() => {
    const deposit = this.deposit();
    if (!deposit) {
      return null;
    }
    if (this.secondsLeft() === 0) {
      if (deposit.status === 'pending') {
        return 'expired';
      }
      if (deposit.status === 'partial') {
        return 'contact_support';
      }
    }
    return deposit.status;
  });

  protected readonly step = computed(() => {
    switch (this.view()) {
      case 'confirming':
      case 'partial':
      case 'contact_support':
        return 1;
      case 'paid':
        return 2;
      default:
        return 0;
    }
  });

  protected readonly expected = computed(() => {
    const deposit = this.deposit();
    return deposit?.expected_amount ? formatUnits(deposit.expected_amount, deposit.decimals) : null;
  });

  /** How long the deposit's discount still runs, ticking each second: "2d 3h 14m", "03:14:05" or null. */
  protected readonly discountLeft = computed(() => {
    const discount = this.deposit()?.discount;
    if (!discount || discount.status !== 'active') {
      return null;
    }
    const seconds = Math.floor((new Date(discount.ends_at).getTime() - this.now()) / 1000);
    if (seconds <= 0) {
      return { text: 'ended', soon: true };
    }
    const days = Math.floor(seconds / 86_400);
    const hours = Math.floor((seconds % 86_400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const pad = (n: number) => String(n).padStart(2, '0');
    const text = days
      ? `ends in ${days}d ${hours}h ${minutes}m`
      : `ends in ${pad(hours)}:${pad(minutes)}:${pad(seconds % 60)}`;
    return { text, soon: seconds < 3600 };
  });

  /** The full price, for a deposit opened during a discount. */
  protected readonly fullPrice = computed(() => {
    const d = this.deposit();
    return d?.discount ? formatUnits(d.discount.full_amount, d.decimals) : null;
  });

  protected readonly received = computed(() => {
    const deposit = this.deposit();
    return deposit ? formatUnits(deposit.received_amount, deposit.decimals) : '0';
  });

  /** What is still to send after an underpayment. */
  protected readonly missing = computed(() => {
    const deposit = this.deposit();
    if (!deposit?.expected_amount) {
      return null;
    }
    const rest = BigInt(deposit.expected_amount) - BigInt(deposit.received_amount);
    return formatUnits((rest > 0n ? rest : 0n).toString(), deposit.decimals);
  });

  constructor() {
    this.title.setTitle('HDPay - Deposit address');
    interval(1000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.now.set(Date.now()));

    // Poll until the payment is final, starting over whenever the id in the URL changes
    // (a newly generated address reuses this page). A failed poll keeps the last good state.
    toObservable(this.id)
      .pipe(
        tap(() => {
          this.deposit.set(null);
          this.notFound.set(false);
          this.error.set(null);
        }),
        switchMap((id) => this.poll(id)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((deposit) => {
        if (deposit) {
          this.deposit.set(deposit);
          this.error.set(null);
        }
      });
  }

  private poll(id: string): Observable<DepositInterface | null> {
    return timer(0, POLL_MS).pipe(
      switchMap(() =>
        this.depositService.find(id).pipe(
          catchError((response: HttpErrorResponse) => {
            if (response.status === 404 || response.status === 422) {
              this.notFound.set(true);
            } else {
              this.error.set(errorMessage(response));
            }
            return of(null);
          })
        )
      ),
      takeWhile(() => !this.notFound()),
      takeWhile((deposit) => deposit?.status !== 'paid', true)
    );
  }

  /**
   * The same account, network and asset at the next index. The old address stays watched, so
   * anything still sent to it is credited, but it is never handed out again.
   */
  protected renew(): void {
    const d = this.deposit();
    if (!d) {
      return;
    }
    this.renewing.set(true);
    this.depositService
      .renew(d.id)
      .subscribe({
        next: (fresh) => {
          this.renewing.set(false);
          void this.router.navigate(['/pay', fresh.id]);
        },
        error: (response: HttpErrorResponse) => {
          this.renewing.set(false);
          this.message.error(errorMessage(response));
        }
      });
  }

  /**
   * Cancel this pending address and go back to the checkout, on the payment step with the same buyer
   * and product, to choose another network or coin. Anything already sent to it is still counted.
   */
  protected cancel(): void {
    const d = this.deposit();
    if (!d) {
      return;
    }
    this.cancelling.set(true);
    this.depositService.cancel(d.id).subscribe({
      next: () => {
        this.cancelling.set(false);
        this.again(d);
      },
      error: (response: HttpErrorResponse) => {
        this.cancelling.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  /**
   * The customer underpaid and the time for the rest ran out: ask the shop for more time. The same
   * address waits for the rest again (partial), with a new deadline; the shop also emails it.
   */
  protected reopen(): void {
    const d = this.deposit();
    if (!d) {
      return;
    }
    this.reopening.set(true);
    this.depositService.reopen(d.id).subscribe({
      next: (deposit) => {
        this.reopening.set(false);
        this.deposit.set(deposit);
        this.message.success('You have more time: send the rest to the same address.');
      },
      error: (response: HttpErrorResponse) => {
        this.reopening.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  /** Back to the checkout with the same buyer and product, to choose how to pay. */
  protected again(d: DepositInterface): void {
    void this.router.navigate(['/'], { state: { again: { account_id: d.account_id, product: d.product } } });
  }

  protected copy(value: string, what: string): void {
    navigator.clipboard.writeText(value).then(
      () => this.message.success(`${what} copied`),
      () => this.message.error('Copy failed. Select it and copy it by hand.')
    );
  }
}
