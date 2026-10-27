import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import {
  AbstractControl,
  FormBuilder,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators
} from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink } from '@angular/router';
import { catchError, distinctUntilChanged, interval, map, of, switchMap } from 'rxjs';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzStepsModule } from 'ng-zorro-antd/steps';

import { formatUnits } from '../../modules/amount';
import { errorMessage } from '../../modules/error';
import { ChainInterface, DepositInterface, ProductInterface, UserInterface } from '../../services/api.interface';
import { ChainService } from '../../services/chain/chain.service';
import { DepositService } from '../../services/deposit/deposit.service';
import { ProductService } from '../../services/product/product.service';
import { UserService } from '../../services/user/user.service';

/** A discount end must be later than now. */
function future(control: AbstractControl): ValidationErrors | null {
  return control.value && new Date(control.value).getTime() <= Date.now() ? { past: true } : null;
}

/** A discount is a percent and an end together, or neither. */
function discountPair(group: AbstractControl): ValidationErrors | null {
  const { discount_percent, discount_ends_at } = group.value;
  const percent = discount_percent !== null && discount_percent !== undefined && discount_percent !== '';
  return percent !== !!discount_ends_at ? { discountPair: true } : null;
}

/** A way to pay: a USD stablecoin on one network. */
interface PaymentMethod {
  chain: string;
  chainName: string;
  asset: string;
  standard: string | null;
}

/**
 * The example shop's checkout: who is buying, what, and on which network. Products have a price and
 * are paid in USDT or USDC, so once all is chosen the shop's API opens the address by itself and
 * emails it to the user. The page talks only to the shop's API (example/api), never to HDPay.
 */
@Component({
  selector: 'hdpay-home',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzFormModule,
    NzInputModule,
    NzSelectModule,
    NzStepsModule,
    NzTagModule
  ],
  templateUrl: './home.component.html',
  styleUrl: './home.component.css'
})
export class HomeComponent {
  private readonly formBuilder = inject(FormBuilder);
  private readonly userService = inject(UserService);
  private readonly chainService = inject(ChainService);
  private readonly productService = inject(ProductService);
  private readonly depositService = inject(DepositService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly year = new Date().getFullYear();
  /** The buyer's short payments whose time for the rest ran out: finished or changed before a new one. */
  protected readonly unfinished = signal<DepositInterface[]>([]);
  protected readonly reopening = signal<string | null>(null);
  protected readonly formatUnits = formatUnits;
  protected readonly users = signal<UserInterface[]>([]);
  /** Buyers from public/users.json: picked from the list, sent like a custom user. */
  protected readonly fileUsers = signal<UserInterface[]>([]);
  protected readonly FILE = 'file:';
  /** The "Custom user" entry in the list: a buyer typed in, for this order only. */
  protected readonly CUSTOM = 'custom';
  protected readonly customForm: FormGroup = this.formBuilder.group({
    name: [null, [Validators.required, Validators.maxLength(120)]],
    email: [null, [Validators.required, Validators.email, Validators.maxLength(254)]],
    account_id: [null, [Validators.required, Validators.pattern(/^\d{1,10}$/)]]
  });
  private readonly customValue = toSignal(this.customForm.valueChanges, { initialValue: this.customForm.value });
  protected readonly chains = signal<ChainInterface[]>([]);
  protected readonly products = signal<ProductInterface[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly formGroup: FormGroup = this.formBuilder.group(
    {
      account_id: [null, [Validators.required]],
      product: [null, [Validators.required]],
      chain: [null, [Validators.required]],
      asset: [null, [Validators.required]],
      // the customer's discount, if the shop gives one: HDPay keeps it with the address
      discount_percent: [null, [Validators.min(1), Validators.max(99), Validators.pattern(/^\d+$/)]],
      discount_ends_at: [null, [future]]
    },
    { validators: discountPair }
  );

  /** The steps, in order: each must be chosen before Next. */
  protected readonly steps = ['User', 'Payment', 'Review'];
  protected readonly step = signal(0);

  private readonly accountValue = toSignal(this.formGroup.controls['account_id'].valueChanges, { initialValue: null });
  private readonly productValue = toSignal(this.formGroup.controls['product'].valueChanges, { initialValue: null });
  private readonly chainValue = toSignal(this.formGroup.controls['chain'].valueChanges, { initialValue: null });
  private readonly assetValue = toSignal(this.formGroup.controls['asset'].valueChanges, { initialValue: null });

  protected readonly custom = computed(() => this.accountValue() === this.CUSTOM);
  /** The buyer chosen from users.json, if one is. */
  protected readonly fileUser = computed(() => {
    const value = this.accountValue() as string | null;
    return value?.startsWith(this.FILE)
      ? (this.fileUsers().find((u) => this.FILE + u.account_id === value) ?? null)
      : null;
  });
  /** The chosen user, a buyer from users.json, or the custom buyer once their fields are filled in. */
  protected readonly user = computed<UserInterface | null>(() => {
    if (this.fileUser()) {
      return this.fileUser();
    }
    if (!this.custom()) {
      return this.users().find((u) => u.account_id === this.accountValue()) ?? null;
    }
    const { name, email, account_id } = this.customValue();
    return this.customForm.valid ? { name: name.trim(), email: email.trim(), account_id: account_id.trim() } : null;
  });
  /** The chosen product, while it is still for sale. */
  protected readonly product = computed(() => this.products().find((p) => p.value === this.productValue()) ?? null);
  /** Every USD stablecoin on every network the project accepts: products are paid in one. */
  protected readonly methods = computed<PaymentMethod[]>(() =>
    this.chains().flatMap((c) =>
      c.assets.filter((a) => a.usd).map((a) => ({ chain: c.key, chainName: c.name, asset: a.symbol, standard: a.standard }))
    )
  );
  /** The chosen payment method, while it is still accepted. */
  protected readonly method = computed(
    () => this.methods().find((m) => m.chain === this.chainValue() && m.asset === this.assetValue()) ?? null
  );

  private readonly percentValue = toSignal(this.formGroup.controls['discount_percent'].valueChanges, {
    initialValue: null
  });

  /** The price less the discount, to the cent half up, as HDPay works it out; null without a discount. */
  protected readonly discounted = computed(() => this.priceAfter(this.product()?.price));

  /** The percent being typed, while it is a valid one (1 to 99); null otherwise. */
  protected readonly percent = computed(() => {
    const percent = Number(this.percentValue());
    return this.percentValue() !== null && Number.isInteger(percent) && percent >= 1 && percent <= 99 ? percent : null;
  });

  /** A price less the percent being typed, to the cent half up as HDPay works it out; null without one. */
  protected priceAfter(price: string | null | undefined): string | null {
    const percent = this.percent();
    if (!price || percent === null) {
      return null;
    }
    const cents = Math.round(Number(price) * 100 * (100 - percent)) / 100;
    return (Math.round(cents) / 100).toFixed(2);
  }

  /** Whether the current step has its choice, so Next can be pressed. */
  private readonly formChanges = toSignal(this.formGroup.valueChanges, { initialValue: null });

  /** The clock, a tick a second, for the discount's countdown. */
  private readonly now = toSignal(interval(1000).pipe(map(() => Date.now())), { initialValue: Date.now() });

  /** How long the discount being given still runs: "2d 3h 14m", "03:14:05", or "ended". */
  protected readonly discountLeft = computed(() => {
    this.formChanges();
    const end = this.formGroup.value.discount_ends_at;
    if (!end) {
      return null;
    }
    const seconds = Math.floor((new Date(end).getTime() - this.now()) / 1000);
    if (seconds <= 0) {
      return { text: 'ended', soon: true, ended: true };
    }
    const days = Math.floor(seconds / 86_400);
    const hours = Math.floor((seconds % 86_400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const pad = (n: number) => String(n).padStart(2, '0');
    const text = days
      ? `ends in ${days}d ${hours}h ${minutes}m`
      : `ends in ${pad(hours)}:${pad(minutes)}:${pad(seconds % 60)}`;
    return { text, soon: seconds < 3600, ended: false };
  });
  /** The discount on the product step: a valid percent and end together, or neither. */
  protected readonly discountOk = computed(() => {
    this.formChanges();
    const controls = this.formGroup.controls;
    return controls['discount_percent'].valid && controls['discount_ends_at'].valid && !this.formGroup.errors?.['discountPair'];
  });
  protected readonly stepDone = computed(
    () => [!!this.user(), !!this.product() && this.discountOk() && !!this.method(), true][this.step()]
  );

  /** Back from a cancelled payment: the same buyer and product, to choose how to pay (history state). */
  private again: { account_id: string; product: string | null } | null =
    (history.state as { again?: { account_id: string; product: string | null } } | null)?.again ?? null;

  /** Once the buyer is in a list: choose them and the product, and go on to the payment step. */
  private resume(): void {
    const again = this.again;
    if (!again) {
      return;
    }
    const value = this.users().some((u) => u.account_id === again.account_id)
      ? again.account_id
      : this.fileUsers().some((u) => u.account_id === again.account_id)
        ? this.FILE + again.account_id
        : null;
    if (value === null) {
      return;   // a custom buyer, typed in: they choose again
    }
    this.again = null;
    this.formGroup.patchValue({ account_id: value, product: again.product, chain: null, asset: null });
    this.step.set(1);
  }

  /** What is still to send on an unfinished payment, for its own product. */
  protected rest(d: DepositInterface): string {
    const rest = BigInt(d.expected_amount ?? '0') - BigInt(d.received_amount);
    return formatUnits((rest > 0n ? rest : 0n).toString(), d.decimals);
  }

  /** What was sent already pays `product`: switching to it needs no more money (HDPay confirms it). */
  protected covers(d: DepositInterface, product: ProductInterface): boolean {
    return !!product.price && Number(formatUnits(d.received_amount, d.decimals)) >= Number(product.price);
  }

  /** What is left to send if the unfinished payment is used for `product`. */
  protected restFor(d: DepositInterface, product: ProductInterface): string {
    const rest = Number(product.price) - Number(formatUnits(d.received_amount, d.decimals));
    return rest > 0 ? rest.toFixed(2) : '0';
  }

  /**
   * Finish an unfinished payment: the rest for its own product, or, with `product`, another one, what
   * was sent counting toward its price. The same address, on its payment page.
   */
  protected finish(d: DepositInterface, product?: string): void {
    this.reopening.set(d.id + (product ?? ''));
    this.error.set(null);
    this.depositService.reopen(d.id, product).subscribe({
      next: (deposit) => {
        this.reopening.set(null);
        void this.router.navigate(['/pay', deposit.id]);
      },
      error: (response: HttpErrorResponse) => {
        this.reopening.set(null);
        this.error.set(errorMessage(response));
      }
    });
  }

  constructor() {
    this.title.setTitle('HDPay - Checkout');

    // a buyer who comes back with an unfinished payment sees it on the payment step, before paying again
    toObservable(this.user)
      .pipe(
        map((user) => user?.account_id ?? null),
        distinctUntilChanged(),
        switchMap((accountId) =>
          accountId ? this.depositService.unfinished(accountId).pipe(catchError(() => of([]))) : of([])
        ),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((deposits) => this.unfinished.set(deposits));

    this.userService
      .fromFile()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((users) => {
        this.fileUsers.set(users);
        this.resume();
      });

    this.userService
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (users) => {
          this.users.set(users);
          this.resume();
        },
        error: (response: HttpErrorResponse) => this.error.set(errorMessage(response))
      });

    // products and networks are followed, so a change in HDPay's admin shows up without a reload
    this.productService
      .watch((response) => this.products().length || this.error.set(errorMessage(response)))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((products) => {
        this.products.set(products);
        const control = this.formGroup.controls['product'];
        if (control.value !== null && !products.some((p) => p.value === control.value)) {
          control.reset();   // no longer for sale
        }
      });

    this.chainService
      .watch((response) => this.chains().length || this.error.set(errorMessage(response)))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((chains) => {
        this.chains.set(chains);
        if (this.formGroup.controls['chain'].value !== null && !this.method()) {
          this.formGroup.patchValue({ chain: null, asset: null });   // no longer accepted
        }
      });
  }

  protected choose(product: ProductInterface): void {
    this.formGroup.controls['product'].setValue(product.value);
  }

  protected pay(method: PaymentMethod): void {
    this.formGroup.patchValue({ chain: method.chain, asset: method.asset });
  }

  protected isChosen(method: PaymentMethod): boolean {
    const chosen = this.method();
    return chosen?.chain === method.chain && chosen?.asset === method.asset;
  }

  protected next(): void {
    if (this.stepDone() && this.step() < this.steps.length - 1) {
      this.step.update((s) => s + 1);
    }
  }

  protected back(): void {
    this.step.update((s) => Math.max(0, s - 1));
  }

  /** A step already passed can be gone back to from the step bar; later ones open with Next. */
  protected goTo(index: number): void {
    if (index < this.step() && !this.loading()) {
      this.step.set(index);
    }
  }

  /** The last step: the shop's API opens the address, at the product's price, and emails it. */
  protected submit(): void {
    if (this.formGroup.invalid || this.loading() || this.discountLeft()?.ended) {
      return;
    }
    const { product, chain, asset, discount_percent, discount_ends_at } = this.formGroup.value;
    const buyer = this.user();
    if (!buyer) {
      return;
    }
    // a custom buyer or one from users.json goes with the order; the shop's users by the account ID alone
    const who = this.custom() || this.fileUser() ? { account_id: buyer.account_id, name: buyer.name, email: buyer.email } : { account_id: buyer.account_id };
    const discount = discount_percent
      ? { discount_percent: Number(discount_percent), discount_ends_at: new Date(discount_ends_at).toISOString() }
      : {};
    this.error.set(null);
    this.loading.set(true);
    this.depositService.open({ ...who, product, chain, asset, ...discount }).subscribe({
      next: (deposit) => {
        this.loading.set(false);
        void this.router.navigate(['/pay', deposit.id]);
      },
      error: (response: HttpErrorResponse) => {
        this.loading.set(false);
        this.error.set(errorMessage(response));
      }
    });
  }
}
