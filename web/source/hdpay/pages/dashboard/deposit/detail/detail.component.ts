import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzDescriptionsModule } from 'ng-zorro-antd/descriptions';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzTagModule } from 'ng-zorro-antd/tag';

import { ReopenComponent } from '../../../../components/reopen/reopen.component';
import { environment } from '../../../../../environments/environment';
import { formatUnits } from '../../../../modules/amount';
import { deleteWarning } from '../../../../modules/deposit';
import { DISCOUNT_STATUS, discountState, owed } from '../../../../modules/discount';
import { errorMessage } from '../../../../modules/error';
import { live } from '../../../../modules/live';
import { STATUS } from '../../../../modules/status';
import { DepositInterface } from '../../../../services/api.interface';
import { AdminService } from '../../../../services/admin/admin.service';
import { AuthService } from '../../../../services/auth/auth.service';

@Component({
  selector: 'hdpay-deposit-detail',
  imports: [
    DatePipe,
    NgTemplateOutlet,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzDescriptionsModule,
    NzIconModule,
    NzPopconfirmModule,
    NzSkeletonModule,
    NzTagModule,
    ReopenComponent
  ],
  templateUrl: './detail.component.html',
  styleUrl: './detail.component.css'
})
export class DetailComponent implements OnInit {
  private readonly adminService = inject(AdminService);
  protected readonly auth = inject(AuthService);
  private readonly message = inject(NzMessageService);
  private readonly title = inject(Title);

  /** The deposit id, bound from the :id route parameter. */
  readonly id = input.required<string>();

  protected readonly deposit = signal<DepositInterface | null>(null);
  protected readonly loading = signal(false);
  protected readonly crediting = signal(false);
  protected readonly reopenOpen = signal(false);
  protected readonly releasing = signal(false);
  protected readonly deleting = signal(false);
  protected readonly deleteWarning = deleteWarning;
  private readonly router = inject(Router);
  protected readonly error = signal<string | null>(null);
  protected readonly status = STATUS;
  protected readonly discountStatus = DISCOUNT_STATUS;
  protected readonly discountState = discountState;
  protected readonly formatUnits = formatUnits;

  /** Owners only. */
  protected remove(d: DepositInterface): void {
    this.deleting.set(true);
    this.adminService.deleteDeposit(d.id).subscribe({
      next: () => {
        this.deleting.set(false);
        this.message.success('Deposit address deleted');
        void this.router.navigate(['/dashboard/deposit']);
      },
      error: (response: HttpErrorResponse) => {
        this.deleting.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  /** How much the discount takes off, in base units. */
  protected saving(discount: { full_amount: string; amount: string }): string {
    return (BigInt(discount.full_amount) - BigInt(discount.amount)).toString();
  }

  /** "in 2 days", "in 3 hours" or "ended 5 minutes ago". */
  protected discountEndsIn(endsAt: string): string {
    const minutes = Math.round((Date.parse(endsAt) - Date.now()) / 60_000);
    const size = Math.abs(minutes);
    const text = size >= 2880 ? `${Math.round(size / 1440)} days` : size >= 120 ? `${Math.round(size / 60)} hours` : `${size} minutes`;
    return minutes >= 0 ? `in ${text}` : `ended ${text} ago`;
  }

  /** What the customer still owes now, with the discount rules applied; null for any amount. */
  protected readonly owedNow = computed(() => {
    const d = this.deposit();
    const rest = d ? owed(d) : null;
    return d && rest !== null ? formatUnits(rest, d.decimals) : null;
  });

  protected readonly checkoutUrl = computed(() => {
    const deposit = this.deposit();
    return deposit && environment.webUrl ? `${environment.webUrl}/pay/${deposit.id}` : null;
  });

  protected readonly expected = computed(() => {
    const d = this.deposit();
    return d?.expected_amount ? formatUnits(d.expected_amount, d.decimals) : null;
  });

  protected readonly received = computed(() => {
    const d = this.deposit();
    return d ? formatUnits(d.received_amount, d.decimals) : '0';
  });

  /** What the customer still has to send: the expected amount less what came. */
  protected readonly owed = computed(() => {
    const d = this.deposit();
    if (!d?.expected_amount) {
      return null;
    }
    const rest = BigInt(d.expected_amount) - BigInt(d.received_amount);
    return formatUnits((rest > 0n ? rest : 0n).toString(), d.decimals);
  });

  constructor() {
    this.title.setTitle('HDPay Admin - Deposit');
    live(() => this.reopenOpen() || this.deleting() || this.load(true), inject(DestroyRef));
  }

  ngOnInit(): void {
    this.load();
  }

  /** `quiet`: a background reload, without the spinner. */
  protected load(quiet = false): void {
    if (!quiet) {
      this.loading.set(true);
      this.error.set(null);
    }
    this.adminService.deposit(this.id()).subscribe({
      next: (deposit) => {
        this.deposit.set(deposit);
        this.error.set(null);
        this.loading.set(false);
      },
      error: (response: HttpErrorResponse) => {
        this.error.set(errorMessage(response));
        this.loading.set(false);
      }
    });
  }

  protected onReopened(deposit: DepositInterface): void {
    this.reopenOpen.set(false);
    this.deposit.set(deposit);
    this.message.success('Reopened: the address is watched for the rest again, and the project was sent deposit.reopened.');
  }

  protected credit(): void {
    this.crediting.set(true);
    this.adminService.credit(this.id()).subscribe({
      next: (deposit) => {
        this.deposit.set(deposit);
        this.crediting.set(false);
        this.message.success('Deposit credited. The sweeper will move the funds.');
      },
      error: (response: HttpErrorResponse) => {
        this.crediting.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  protected release(): void {
    this.releasing.set(true);
    this.adminService.release(this.id()).subscribe({
      next: (deposit) => {
        this.deposit.set(deposit);
        this.releasing.set(false);
        this.message.success('Released. The sweeper tries again on its next pass.');
      },
      error: (response: HttpErrorResponse) => {
        this.releasing.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  protected copy(value: string, what: string): void {
    navigator.clipboard.writeText(value).then(
      () => this.message.success(`${what} copied`),
      () => this.message.error('Copy failed. Select it and copy it by hand.')
    );
  }
}
