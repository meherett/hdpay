import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule, NzTableQueryParams } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';
import { Subject, catchError, debounceTime, distinctUntilChanged, of, switchMap, tap } from 'rxjs';

import { ReopenComponent } from '../../../components/reopen/reopen.component';
import { difference, formatUnits } from '../../../modules/amount';
import { deleteWarning } from '../../../modules/deposit';
import { DISCOUNT_STATUS, discountState } from '../../../modules/discount';
import { errorMessage } from '../../../modules/error';
import { live } from '../../../modules/live';
import { STATUS, STATUSES } from '../../../modules/status';
import {
  ChainInterface,
  DepositFilterInterface,
  DepositInterface,
  DepositListInterface,
  InternalStatus,
  ProjectInterface
} from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';
import { AuthService } from '../../../services/auth/auth.service';
import { ChainService } from '../../../services/chain/chain.service';

@Component({
  selector: 'hdpay-deposit',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzPopconfirmModule,
    ReopenComponent,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule
  ],
  templateUrl: './deposit.component.html',
  styleUrl: './deposit.component.css'
})
export class DepositComponent {
  private readonly adminService = inject(AdminService);
  private readonly chainService = inject(ChainService);
  private readonly message = inject(NzMessageService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly auth = inject(AuthService);

  /** ?status=… and ?project_id=… from the overview's status cards. */
  readonly status = input<string>();
  readonly project_id = input<string>();

  protected readonly deposits = signal<DepositInterface[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(false);
  protected readonly pageIndex = signal(1);
  protected readonly pageSize = signal(20);
  /** Every network in the chains file, for the filters. */
  protected readonly chains = signal<ChainInterface[]>([]);
  protected readonly projects = signal<ProjectInterface[]>([]);
  protected readonly statuses = STATUSES;
  protected readonly statusInfo = STATUS;
  protected readonly discountStatus = DISCOUNT_STATUS;
  protected readonly discountState = discountState;
  protected readonly formatUnits = formatUnits;
  protected readonly difference = difference;

  protected search = '';
  protected projectFilter: string | null = null;
  protected readonly chain = signal<string | null>(null);
  /** native, or a token standard (ERC20, BEP20, TRC20). */
  protected readonly type = signal<string | null>(null);

  /** The token standards of every chain, for the type filter: ERC20, BEP20, TRC20... */
  protected readonly standards = computed(() =>
    [...new Set(this.chains().flatMap((c) => c.assets.map((a) => a.standard)).filter((s): s is string => !!s))].sort()
  );
  protected asset: string | null = null;
  protected statusFilter: InternalStatus | null = null;
  protected differenceFilter: 'over' | 'under' | null = null;

  /** Asset symbols for the asset filter: those of the chosen network and type, or of every chain. */
  protected readonly assetSymbols = computed(() => {
    const chains = this.chain() ? this.chains().filter((c) => c.key === this.chain()) : this.chains();
    const assets = chains
      .flatMap((c) => c.assets)
      .filter((a) => !this.type() || (this.type() === 'native' ? a.contract === null : a.standard === this.type()));
    return [...new Set(assets.map((a) => a.symbol))].sort();
  });

  private tableReady = false;
  private readonly searchChanges = new Subject<string>();

  /**
   * Every load goes through here. switchMap cancels the request in flight, so
   * a slow earlier response can never overwrite the results of a later one.
   */
  private readonly loads = new Subject<{ filter: DepositFilterInterface; quiet: boolean }>();

  constructor() {
    this.title.setTitle('HDPay Admin - Deposits');

    this.adminService
      .projects()
      .subscribe({ next: (projects) => this.projects.set(projects.filter((p) => p.status !== 'locked')) });

    // follows the chains file: a chain or asset added or removed there shows up here
    this.chainService
      .watch((response) => {
        if (!this.chains().length) {
          this.message.error(errorMessage(response));
        }
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((chains) => this.onChains(chains));

    this.loads
      .pipe(
        tap(({ quiet }) => quiet || this.loading.set(true)),
        switchMap(({ filter, quiet }) =>
          this.adminService.deposits(filter).pipe(
            catchError((response: HttpErrorResponse) => {
              if (!quiet) {
                this.message.error(errorMessage(response));   // a background reload just tries again
              }
              return of(null);
            })
          )
        ),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((response: DepositListInterface | null) => {
        if (response) {
          this.deposits.set(response.items);
          this.total.set(response.total);
        }
        this.loading.set(false);
      });

    this.searchChanges
      .pipe(debounceTime(350), distinctUntilChanged(), takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.onFilter());

    // new and changed deposits show up by themselves, on the page and filters in use
    live(() => this.tableReady && this.load(true), this.destroyRef);

    // The table loads the first page itself; later ?status= / ?project_id= changes reload here.
    effect(() => {
      const status = this.status();
      const projectId = this.project_id();
      untracked(() => {
        this.statusFilter = STATUSES.includes(status as InternalStatus) ? (status as InternalStatus) : null;
        this.projectFilter = projectId || null;
        if (this.tableReady) {
          this.onFilter();
        }
      });
    });
  }

  /** New chains: drop filters and choices that no longer exist. */
  private onChains(chains: ChainInterface[]): void {
    this.chains.set(chains);
    let refilter = false;
    if (this.chain() && !chains.some((c) => c.key === this.chain())) {
      this.chain.set(null);
      refilter = true;
    }
    if (this.asset && !this.assetSymbols().includes(this.asset)) {
      this.asset = null;
      refilter = true;
    }
    if (refilter && this.tableReady) {
      this.onFilter();
    }
  }

  protected onSearch(value: string): void {
    this.searchChanges.next(value.trim());
  }

  protected onFilter(): void {
    // an asset the chosen network or type does not have would match nothing
    if (this.asset && !this.assetSymbols().includes(this.asset)) {
      this.asset = null;
    }
    this.pageIndex.set(1);
    this.load();
  }

  /** nz-table fires this on first render and on every page or size change. */
  protected onQueryParams(params: NzTableQueryParams): void {
    this.tableReady = true;
    this.pageIndex.set(params.pageIndex);
    this.pageSize.set(params.pageSize);
    this.load();
  }

  /** The deposit in the reopen dialog, null while it is closed. */
  protected readonly reopenTarget = signal<DepositInterface | null>(null);

  protected readonly deleteWarning = deleteWarning;
  protected readonly deletingId = signal<string | null>(null);

  /** Owners only: the address is gone from HDPay, and the list reloads. */
  protected remove(deposit: DepositInterface): void {
    this.deletingId.set(deposit.id);
    this.adminService.deleteDeposit(deposit.id).subscribe({
      next: () => {
        this.deletingId.set(null);
        this.message.success(`Deleted the ${deposit.asset} address of account ${deposit.account_id}`);
        this.load();
      },
      error: (response: HttpErrorResponse) => {
        this.deletingId.set(null);
        this.message.error(errorMessage(response));
      }
    });
  }

  protected onReopened(deposit: DepositInterface): void {
    this.reopenTarget.set(null);
    this.message.success(`Reopened: account ${deposit.account_id} can send the rest to the same address.`);
    this.load();
  }

  /** `quiet`: a background reload, without the spinner. */
  protected load(quiet = false): void {
    this.loads.next({ quiet, filter: {
      skip: (this.pageIndex() - 1) * this.pageSize(),
      limit: this.pageSize(),
      search: this.search.trim() || undefined,
      project_id: this.projectFilter,
      chain: this.chain(),
      type: this.type(),
      asset: this.asset,
      status: this.statusFilter,
      difference: this.differenceFilter
    } });
  }

  protected reset(): void {
    this.search = '';
    this.projectFilter = null;
    this.chain.set(null);
    this.type.set(null);
    this.asset = null;
    this.statusFilter = null;
    this.differenceFilter = null;
    void this.router.navigate([], { queryParams: {} });
    this.onFilter();
  }

  protected short(value: string): string {
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
  }
}
