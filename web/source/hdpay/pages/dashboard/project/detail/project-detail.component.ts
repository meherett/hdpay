import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTabsModule } from 'ng-zorro-antd/tabs';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';

import { DeleteProjectComponent } from '../../../../components/delete-project/delete-project.component';
import { ProjectFormComponent } from '../../../../components/project-form/project-form.component';
import { formatUnits } from '../../../../modules/amount';
import { errorMessage } from '../../../../modules/error';
import { live } from '../../../../modules/live';
import { revealErrors } from '../../../../modules/form';
import { PROJECT_STATUS, STATUS } from '../../../../modules/status';
import {
  AdminDefaultsInterface,
  ApiKeyInterface,
  ChainInterface,
  CreatedApiKeyInterface,
  EventInterface,
  FunderBalancesInterface,
  ProjectFieldsInterface,
  ProjectInterface,
  TreasuryInterface
} from '../../../../services/api.interface';
import { AdminService } from '../../../../services/admin/admin.service';
import { AuthService } from '../../../../services/auth/auth.service';
import { ChainService } from '../../../../services/chain/chain.service';

const FAMILIES = ['evm', 'tron'] as const;

/** A project: its keys, API keys, settings, balances and events. `new` creates one. */
/** The stablecoins a treasury's USD total counts. */
const USD_ASSETS = ['USDT', 'USDC'];
const FAMILY_NAMES: Record<string, string> = { evm: 'EVM', tron: 'Tron' };

/** A treasury or fee funder balance; its symbol is under `asset` or `symbol`. */
interface BalanceRow {
  chain: string;
  chain_name: string;
  address_url: string;
  decimals: number;
  balance: string | null;
  error: string | null;
  [key: string]: string | number | null;
}

interface NetworkGroup {
  chain: string;
  chain_name: string;
  address_url: string;
  rows: BalanceRow[];
}

@Component({
  selector: 'hdpay-project-detail',
  imports: [
    DatePipe,
    NgTemplateOutlet,
    ReactiveFormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzFormModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzPopconfirmModule,
    NzSelectModule,
    NzSkeletonModule,
    NzTableModule,
    NzTabsModule,
    NzTagModule,
    NzTooltipModule,
    DeleteProjectComponent,
    ProjectFormComponent
  ],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.css'
})
export class ProjectDetailComponent {
  private readonly adminService = inject(AdminService);
  private readonly chainService = inject(ChainService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly message = inject(NzMessageService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);
  protected readonly auth = inject(AuthService);

  /** The project id from the route, or `new`. */
  readonly id = input.required<string>();

  protected readonly families = FAMILIES;
  protected readonly projectStatus = PROJECT_STATUS;
  protected readonly status = STATUS;
  protected readonly formatUnits = formatUnits;

  protected readonly project = signal<ProjectInterface | null>(null);
  protected readonly chains = signal<ChainInterface[]>([]);
  protected readonly defaults = signal<AdminDefaultsInterface | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly isNew = computed(() => this.id() === 'new');
  /** Families whose key is still missing. */
  protected readonly missingKeys = computed(() => FAMILIES.filter((f) => !this.project()?.xpubs[f]));

  // keys
  protected readonly keysForm: FormGroup = this.formBuilder.group({ evm: [null], tron: [null] });
  protected readonly keysError = signal<string | null>(null);

  // API keys
  protected readonly apiKeys = signal<ApiKeyInterface[]>([]);
  protected readonly newKey = signal<CreatedApiKeyInterface | null>(null);
  protected readonly keyForm: FormGroup = this.formBuilder.group({
    kind: ['secret', [Validators.required]],
    name: [null, [Validators.required, Validators.maxLength(120)]]
  });

  // balances and events, loaded on request
  protected readonly treasury = signal<TreasuryInterface[] | null>(null);
  protected readonly funders = signal<FunderBalancesInterface[] | null>(null);
  protected readonly balancesLoading = signal(false);
  protected readonly balancesError = signal<string | null>(null);
  protected readonly balancesAt = signal<Date | null>(null);
  protected readonly balancesOpen = signal(false);
  /** USDT and USDC in the treasuries, on every network, in USD. */
  protected readonly stablecoins = computed(() => {
    let total = 0;
    for (const t of this.treasury() ?? []) {
      for (const b of t.balances) {
        if (b.balance !== null && USD_ASSETS.includes(b.asset)) {
          total += Number(formatUnits(b.balance, b.decimals));
        }
      }
    }
    return total.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  });
  protected readonly treasuriesSet = computed(() => (this.treasury() ?? []).filter((t) => t.address).length);
  /** Fee funder balances at nothing: their token sweeps wait for gas. */
  protected readonly emptyFunders = computed(
    () => (this.funders() ?? []).flatMap((f) => f.balances).filter((b) => b.balance === '0').length
  );
  protected readonly events = signal<EventInterface[]>([]);
  protected readonly eventsLoading = signal(false);

  constructor() {
    // the events the project's backend is sent follow by themselves
    live(() => this.loadEvents(true), inject(DestroyRef));
    // balances are read from the chains: less often, and only while their tab is open
    live(() => (this.balancesOpen() && !this.balancesLoading() ? this.loadBalances() : undefined), inject(DestroyRef), 30_000);
    this.chainService.list().subscribe({ next: (chains) => this.chains.set(chains) });
    this.adminService.defaults().subscribe({ next: (defaults) => this.defaults.set(defaults) });

    effect(() => {
      const id = this.id();
      untracked(() => {
        this.project.set(null);
        this.treasury.set(null);
        this.funders.set(null);
        this.balancesAt.set(null);
        if (id === 'new') {
          this.title.setTitle('HDPay Admin - New project');
          return;
        }
        this.load(id);
        this.loadKeys(id);
        this.loadEvents();
      });
    });
  }

  private load(id: string): void {
    this.error.set(null);
    this.adminService.project(id).subscribe({
      next: (project) => {
        this.project.set(project);
        this.title.setTitle(`HDPay Admin - ${project.name}`);
      },
      error: (response: HttpErrorResponse) => this.error.set(errorMessage(response))
    });
  }

  // ---- create and settings

  protected create(fields: ProjectFieldsInterface): void {
    this.saving.set(true);
    this.error.set(null);
    this.adminService.createProject(fields).subscribe({
      next: (project) => {
        this.saving.set(false);
        this.message.success(`Project created with number ${project.number}. Next: its keys.`);
        void this.router.navigate(['/dashboard/project', project.id]);
      },
      error: (response: HttpErrorResponse) => {
        this.saving.set(false);
        this.error.set(errorMessage(response));
      }
    });
  }

  protected saveSettings(fields: ProjectFieldsInterface): void {
    const project = this.project();
    if (!project) {
      return;
    }
    this.saving.set(true);
    this.adminService.updateProject(project.id, fields).subscribe({
      next: (updated) => {
        this.saving.set(false);
        this.project.set(updated);
        this.message.success('Saved');
      },
      error: (response: HttpErrorResponse) => {
        this.saving.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }

  protected readonly deleteOpen = signal(false);
  /** Deleted from its own page: back to the list. */
  protected onDeleted(): void {
    this.deleteOpen.set(false);
    void this.router.navigate(['/dashboard/project']);
  }

  protected setStatus(status: 'active' | 'disabled'): void {
    const project = this.project();
    if (!project) {
      return;
    }
    this.adminService.setProjectStatus(project.id, status).subscribe({
      next: (updated) => {
        this.project.set(updated);
        this.message.success(status === 'active' ? 'Project enabled' : 'Project disabled: its API keys stop working');
      },
      error: (response: HttpErrorResponse) => this.message.error(errorMessage(response))
    });
  }

  // ---- keys

  protected command(): string {
    return `python -m api.keys project ${this.project()?.number ?? '<number>'}`;
  }

  protected saveKeys(): void {
    const project = this.project();
    if (!project) {
      return;
    }
    const keys: Record<string, string> = {};
    for (const family of FAMILIES) {
      const value = String(this.keysForm.value[family] ?? '').trim();
      if (value) {
        keys[family] = value;
      }
    }
    if (!Object.keys(keys).length) {
      this.keysError.set('Paste at least one key');
      return;
    }
    this.keysError.set(null);
    this.saving.set(true);
    this.adminService.setProjectKeys(project.id, keys).subscribe({
      next: (updated) => {
        this.saving.set(false);
        this.project.set(updated);
        this.keysForm.reset();
        this.message.success(updated.status === 'active' ? 'Keys saved: the project is active' : 'Keys saved');
      },
      error: (response: HttpErrorResponse) => {
        this.saving.set(false);
        this.keysError.set(errorMessage(response));
      }
    });
  }

  // ---- API keys

  private loadKeys(id: string): void {
    this.adminService.apiKeys(id).subscribe({ next: (keys) => this.apiKeys.set(keys) });
  }

  protected createKey(): void {
    const project = this.project();
    if (!project) {
      return;
    }
    if (this.keyForm.invalid) {
      revealErrors(this.keyForm);
      return;
    }
    this.adminService.createApiKey(project.id, this.keyForm.value.kind, String(this.keyForm.value.name).trim()).subscribe({
      next: (created) => {
        this.newKey.set(created);
        this.keyForm.reset({ kind: 'secret' });
        this.loadKeys(project.id);
      },
      error: (response: HttpErrorResponse) => this.message.error(errorMessage(response))
    });
  }

  protected revoke(key: ApiKeyInterface): void {
    const project = this.project();
    if (!project) {
      return;
    }
    this.adminService.revokeApiKey(project.id, key.id).subscribe({
      next: () => {
        this.message.success(`${key.name} revoked`);
        this.loadKeys(project.id);
      },
      error: (response: HttpErrorResponse) => this.message.error(errorMessage(response))
    });
  }

  // ---- balances and events

  protected loadBalances(): void {
    const project = this.project();
    if (!project) {
      return;
    }
    this.balancesLoading.set(true);
    this.balancesError.set(null);
    let pending = 2;
    const done = () => {
      if (--pending === 0) {
        this.balancesLoading.set(false);
        this.balancesAt.set(new Date());
      }
    };
    this.adminService.projectTreasury(project.id).subscribe({
      next: (treasury) => {
        this.treasury.set(treasury);
        done();
      },
      error: (response: HttpErrorResponse) => {
        this.balancesError.set(errorMessage(response));
        done();
      }
    });
    this.adminService.projectFunders(project.id).subscribe({
      next: (funders) => {
        this.funders.set(funders);
        done();
      },
      error: (response: HttpErrorResponse) => {
        this.balancesError.set(errorMessage(response));
        done();
      }
    });
  }

  protected loadEvents(quiet = false): void {
    const id = this.id();
    if (id === 'new') {
      return;
    }
    this.eventsLoading.set(!quiet);
    this.adminService.projectEvents(id).subscribe({
      next: (events) => {
        this.events.set(events);
        this.eventsLoading.set(false);
      },
      error: () => this.eventsLoading.set(false)
    });
  }

  protected openBalances(): void {
    this.balancesOpen.set(true);
    if (this.treasury() === null && !this.balancesLoading()) {
      this.loadBalances();
    }
  }

  protected familyName(family: string): string {
    return FAMILY_NAMES[family] ?? family;
  }

  /** Balance rows grouped by network, in their order. */
  protected byNetwork(rows: BalanceRow[]): NetworkGroup[] {
    const groups: NetworkGroup[] = [];
    for (const row of rows) {
      const group = groups.find((g) => g.chain === row.chain);
      if (group) {
        group.rows.push(row);
      } else {
        groups.push({ chain: row.chain, chain_name: row.chain_name, address_url: row.address_url, rows: [row] });
      }
    }
    return groups;
  }

  protected copy(value: string, what: string): void {
    navigator.clipboard.writeText(value).then(
      () => this.message.success(`${what} copied`),
      () => this.message.error(`Copy failed. Select the ${what.toLowerCase()} and copy it by hand.`)
    );
  }

  /** A website as its host, "shop.example.com". */
  protected host(url: string): string {
    try {
      return new URL(url).host;
    } catch {
      return url;
    }
  }

  protected short(value: string | null): string {
    return value ? `${value.slice(0, 14)}…${value.slice(-8)}` : '';
  }
}
