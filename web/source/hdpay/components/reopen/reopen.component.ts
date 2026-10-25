import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';

import { formatUnits, parseUnits } from '../../modules/amount';
import { discountEnded, discountedPrice } from '../../modules/discount';
import { errorMessage } from '../../modules/error';
import { revealErrors } from '../../modules/form';
import { DepositInterface, ProductInterface } from '../../services/api.interface';
import { AdminService } from '../../services/admin/admin.service';

/** The server allows a window of up to a week. */
const MAX_MINUTES = 7 * 24 * 60;

/**
 * Reopen a deposit: an underpaid one in review (back to partial, waiting for the rest), or an expired
 * one (back to pending, with a new payment window). Support sets what the customer has to send and
 * how long the address waits for it. Open it by setting `deposit`; it emits `reopened` or `closed`.
 */
@Component({
  selector: 'hdpay-reopen',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzFormModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTagModule
  ],
  templateUrl: './reopen.component.html',
  styleUrl: './reopen.component.css'
})
export class ReopenComponent {
  private readonly adminService = inject(AdminService);
  private readonly formBuilder = inject(FormBuilder);

  readonly deposit = input<DepositInterface | null>(null);
  readonly closed = output<void>();
  readonly reopened = output<DepositInterface>();

  protected readonly presets = [
    { label: '30 min', minutes: 30 },
    { label: '1 hour', minutes: 60 },
    { label: '12 hours', minutes: 720 },
    { label: '1 day', minutes: 1440 },
    { label: '1 week', minutes: MAX_MINUTES }
  ];
  /** The preset chosen, in minutes, or 'custom' for minutes typed in. */
  protected readonly window = signal<number | 'custom'>(30);
  protected readonly products = signal<ProductInterface[]>([]);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly formatUnits = formatUnits;
  protected readonly form: FormGroup = this.formBuilder.group({
    product: [null],   // required while products.json lists products
    remaining: [null, [Validators.required, Validators.pattern(/^\d+(\.\d+)?$/)]],
    minutes: [null, [Validators.required, Validators.pattern(/^\d+$/), Validators.min(1), Validators.max(MAX_MINUTES)]],
    keep_discount: [false]
  });

  /** The starting countdown: the remainder window for one in review, the payment window for an expired one. */
  private remainderMinutes = 30;
  private paymentMinutes = 30;

  constructor() {
    this.adminService.defaults().subscribe({
      next: (defaults) => {
        this.remainderMinutes = defaults.remainder_window_minutes;
        this.paymentMinutes = defaults.deposit_ttl_minutes;
      },
      error: () => undefined   // keep 30; the server still checks the value
    });

    // another product, or keeping an ended discount, changes what is still owed
    for (const name of ['product', 'keep_discount']) {
      this.form.controls[name].valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
        const d = this.deposit();
        if (d) {
          this.form.controls['remaining'].setValue(this.shortfall(d));
        }
      });
    }

    // each time it opens: the current shortfall and the usual window
    effect(() => {
      const d = this.deposit();
      if (d) {
        untracked(() => {
          this.error.set(null);
          // a discount that still runs counts anyway: ticked, and not to be changed
          const running = !!d.discount && d.discount.status === 'active' && !discountEnded(d);
          const minutes = this.expired(d) ? this.paymentMinutes : this.remainderMinutes;
          this.form.reset(
            { product: null, remaining: null, minutes, keep_discount: running },
            { emitEvent: false }
          );
          // an expired deposit for any amount has nothing to ask for: only a new window
          const remaining = this.form.controls['remaining'];
          if (this.anyAmount(d)) {
            remaining.disable({ emitEvent: false });
          } else {
            remaining.enable({ emitEvent: false });
          }
          const keep = this.form.controls['keep_discount'];
          if (running) {
            keep.disable({ emitEvent: false });
          } else {
            keep.enable({ emitEvent: false });
          }
          this.window.set(this.presets.some((p) => p.minutes === minutes) ? minutes : 'custom');
          this.form.controls['remaining'].setValue(this.shortfall(d));
          this.loadProducts(d);
        });
      }
    });
  }

  /** The deposit's project's products, starting on the deposit's product when it is still offered. */
  private loadProducts(d: DepositInterface): void {
    this.products.set([]);
    this.adminService.project(d.project_id).subscribe({
      next: ({ products }) => {
        this.products.set(products);
        const control = this.form.controls['product'];
        control.setValidators(products.length ? [Validators.required] : []);
        control.setValue(products.some((p) => p.value === d.product) ? d.product : null);
        control.updateValueAndValidity();
      },
      error: (response: HttpErrorResponse) => this.error.set(errorMessage(response))
    });
  }

  protected readonly endedDiscount = discountEnded;

  /** A discount that has not ended: it counts whatever is chosen here. */
  protected discountRunning(d: DepositInterface): boolean {
    return !!d.discount && d.discount.status === 'active' && !discountEnded(d);
  }

  /**
   * What the customer still owes: the chosen product's price (or the deposit's own, for an unpriced one),
   * less the discount while it counts (still running, kept, or kept by support), less what came.
   */
  private shortfall(d: DepositInterface): string {
    const discount = d.discount;
    const price = this.products().find((p) => p.value === this.form.controls['product'].value)?.price;
    let full = discount?.full_amount ?? d.expected_amount ?? '0';
    let amount = discount?.amount ?? full;
    if (price) {
      full = parseUnits(price, d.decimals) ?? full;
      amount = discount ? (parseUnits(discountedPrice(price, discount.percent), d.decimals) ?? amount) : full;
    }
    const keep = !!this.form.getRawValue().keep_discount;
    const counts = !!discount && (keep || ['kept', 'honored'].includes(discount.status) || this.discountRunning(d));
    const expected = discount ? (counts ? amount : full) : price ? full : (d.expected_amount ?? '0');
    const rest = BigInt(expected) - BigInt(d.received_amount);
    return formatUnits((rest > 0n ? rest : 0n).toString(), d.decimals);
  }

  /** Expired with nothing received: it goes back to pending, with a new payment window. */
  protected expired(d: DepositInterface): boolean {
    return d.internal_status === 'expired';
  }

  /** An expired deposit that accepts any amount: no amount to set. */
  protected anyAmount(d: DepositInterface): boolean {
    return this.expired(d) && !d.expected_amount;
  }

  /** Received + remaining, as the new expected total; null while the amount is not valid. */
  protected newTotal(d: DepositInterface): string | null {
    const remaining = parseUnits(String(this.form.value.remaining ?? ''), d.decimals);
    return remaining === null ? null : formatUnits((BigInt(d.received_amount) + BigInt(remaining)).toString(), d.decimals);
  }

  /** When the countdown would end, for the hint under the field. */
  protected deadline(): Date | null {
    const minutes = Number(this.form.value.minutes);
    return this.form.controls['minutes'].valid ? new Date(Date.now() + minutes * 60_000) : null;
  }

  /** A preset sets the minutes; Custom keeps them, to be typed over. */
  protected chooseWindow(choice: number | 'custom'): void {
    this.window.set(choice);
    if (choice !== 'custom') {
      this.form.controls['minutes'].setValue(choice);
      this.form.controls['minutes'].markAsDirty();
    }
  }

  protected submit(): void {
    const d = this.deposit();
    if (!d) {
      return;
    }
    this.error.set(null);
    if (this.form.invalid) {
      revealErrors(this.form);
      return;
    }
    const remaining = this.anyAmount(d) ? undefined : parseUnits(String(this.form.value.remaining), d.decimals);
    if (remaining === null || remaining === '0') {
      this.error.set(`Enter a positive amount with at most ${d.decimals} decimal places.`);
      return;
    }

    this.saving.set(true);
    const product = this.form.value.product ?? undefined;
    const keep_discount = this.endedDiscount(d) && !!this.form.value.keep_discount;
    this.adminService
      .reopen(d.id, { remaining_amount: remaining, minutes: Number(this.form.value.minutes), product, keep_discount })
      .subscribe({
        next: (deposit) => {
          this.saving.set(false);
          this.reopened.emit(deposit);
        },
        error: (response: HttpErrorResponse) => {
          this.saving.set(false);
          this.error.set(errorMessage(response));
        }
      });
  }
}
