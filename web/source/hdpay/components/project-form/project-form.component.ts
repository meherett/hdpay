import { CdkDrag, CdkDragDrop, CdkDragHandle, CdkDropList, CdkDropListGroup, moveItemInArray } from '@angular/cdk/drag-drop';
import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormArray, FormBuilder, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';

import { revealErrors } from '../../modules/form';
import { AdminDefaultsInterface, ChainInterface, ProjectFieldsInterface, ProjectInterface } from '../../services/api.interface';

const EVM_ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const TRON_ADDRESS = /^T[1-9A-HJ-NP-Za-km-z]{33}$/;
const OWNER_FIELDS = ['evm_treasury', 'tron_treasury', 'allowed_ips'];
const WEBSITE = /^\s*https?:\/\/[^\s/]+\S*\s*$/;

/**
 * Everything a project is set up with: products, accepted networks and assets, treasuries, timing
 * and allowed IPs. Used to create a project (empty) and to edit one. It only collects and checks;
 * the page saves what `save` emits.
 *
 * Products and accepted networks are ordered by dragging; a network moves between Available and
 * Accepted by dragging it across (or with its + and x buttons), and its assets are toggled as tags.
 */
@Component({
  selector: 'hdpay-project-form',
  imports: [
    CdkDrag,
    CdkDragHandle,
    CdkDropList,
    CdkDropListGroup,
    FormsModule,
    ReactiveFormsModule,
    NzAlertModule,
    NzButtonModule,
    NzFormModule,
    NzIconModule,
    NzInputModule,
    NzInputNumberModule,
    NzSelectModule,
    NzTagModule,
    NzTooltipModule
  ],
  templateUrl: './project-form.component.html',
  styleUrl: './project-form.component.css'
})
export class ProjectFormComponent {
  private readonly formBuilder = inject(FormBuilder);

  readonly project = input<ProjectInterface | null>(null);
  readonly chains = input<ChainInterface[]>([]);
  readonly defaults = input<AdminDefaultsInterface | null>(null);
  readonly saving = input(false);
  readonly submitLabel = input('Save');
  readonly readonly = input(false);
  /** The treasury and allowed IPs are an owner's to change: read-only for anyone else. */
  readonly ownerFieldsLocked = input(false);
  readonly save = output<ProjectFieldsInterface>();

  /** Accepted networks, in the order chosen, and the asset symbols accepted on each. */
  protected readonly order = signal<string[]>([]);
  protected readonly accepted = signal<Record<string, string[]>>({});
  protected readonly chainsError = signal<string | null>(null);

  protected readonly acceptedChains = computed(() =>
    this.order()
      .map((key) => this.chains().find((c) => c.key === key))
      .filter((c): c is ChainInterface => !!c)
  );
  protected readonly availableChains = computed(() => this.chains().filter((c) => !this.order().includes(c.key)));

  protected readonly form: FormGroup = this.formBuilder.group({
    name: [null, [Validators.required, Validators.maxLength(120)]],
    description: [null, [Validators.maxLength(500)]],
    website: [null, [Validators.maxLength(200), Validators.pattern(WEBSITE)]],
    products: this.formBuilder.array([]),
    evm_treasury: [null, [Validators.pattern(EVM_ADDRESS)]],
    tron_treasury: [null, [Validators.pattern(TRON_ADDRESS)]],
    deposit_ttl_minutes: [null, [Validators.required, Validators.min(1), Validators.max(10080)]],
    remainder_window_minutes: [null, [Validators.required, Validators.min(1), Validators.max(10080)]],
    amount_tolerance_bps: [null, [Validators.required, Validators.min(0), Validators.max(10000)]],
    allowed_ips: [[]]
  });

  protected get products(): FormArray {
    return this.form.controls['products'] as FormArray;
  }

  constructor() {
    // fill the form from the project, or from the server's defaults for a new one
    effect(() => {
      const project = this.project();
      const defaults = this.defaults();
      untracked(() => this.fill(project, defaults));
    });
    effect(() => {
      if (this.readonly()) {
        this.form.disable({ emitEvent: false });
      } else {
        this.form.enable({ emitEvent: false });
        if (this.ownerFieldsLocked()) {
          for (const name of OWNER_FIELDS) {
            this.form.controls[name].disable({ emitEvent: false });
          }
        }
      }
    });
  }

  private fill(project: ProjectInterface | null, defaults: AdminDefaultsInterface | null): void {
    this.products.clear();
    for (const product of project?.products ?? []) {
      this.addProduct(product.value, product.name, product.subname, product.price ?? null);
    }
    this.form.patchValue({
      name: project?.name ?? null,
      description: project?.description ?? null,
      website: project?.website ?? null,
      evm_treasury: project?.treasury['evm'] ?? null,
      tron_treasury: project?.treasury['tron'] ?? null,
      deposit_ttl_minutes: project?.deposit_ttl_minutes ?? defaults?.deposit_ttl_minutes ?? 30,
      remainder_window_minutes: project?.remainder_window_minutes ?? defaults?.remainder_window_minutes ?? 1440,
      amount_tolerance_bps: project?.amount_tolerance_bps ?? defaults?.amount_tolerance_bps ?? 50,
      allowed_ips: [...(project?.allowed_ips ?? [])]
    });
    const chains = structuredClone(project?.chains ?? {});
    this.accepted.set(chains);
    this.order.set(Object.keys(chains));
    this.chainsError.set(null);
  }

  // ---- products

  protected addProduct(
    value: string | null = null,
    name: string | null = null,
    subname: string | null = null,
    price: string | null = null
  ): void {
    this.products.push(
      this.formBuilder.group({
        value: [value, [Validators.required, Validators.maxLength(120)]],
        name: [name, [Validators.required, Validators.maxLength(120)]],
        subname: [subname, [Validators.maxLength(120)]],
        // USD, paid in USDT or USDC; empty leaves the amount to whoever opens the deposit
        price: [price, [Validators.pattern(/^\d{1,12}(\.\d{1,2})?$/), Validators.min(0.01)]]
      })
    );
  }

  protected removeProduct(index: number): void {
    this.products.removeAt(index);
  }

  /** A product dragged to a new place in the list. */
  protected moveProduct(event: CdkDragDrop<unknown>): void {
    if (event.previousIndex === event.currentIndex) {
      return;
    }
    const control = this.products.at(event.previousIndex);
    this.products.removeAt(event.previousIndex);
    this.products.insert(event.currentIndex, control);
    this.products.markAsDirty();
  }

  // ---- networks and assets

  protected isAccepted(chain: string, asset: string): boolean {
    return (this.accepted()[chain] ?? []).includes(asset);
  }

  protected toggle(chain: ChainInterface, asset: string, on: boolean): void {
    if (this.readonly()) {
      return;
    }
    const current = new Set(this.accepted()[chain.key] ?? []);
    if (on) {
      current.add(asset);
    } else {
      current.delete(asset);
    }
    // the chains file's order; a network with none stays accepted until it is moved back
    const ordered = chain.assets.map((a) => a.symbol).filter((symbol) => current.has(symbol));
    this.accepted.set({ ...this.accepted(), [chain.key]: ordered });
    this.chainsError.set(null);
  }

  /** Accept a network, with all its assets to start with. */
  protected addChain(chain: ChainInterface, at = this.order().length): void {
    if (this.readonly() || this.order().includes(chain.key)) {
      return;
    }
    const order = [...this.order()];
    order.splice(at, 0, chain.key);
    this.order.set(order);
    this.accepted.set({ ...this.accepted(), [chain.key]: chain.assets.map((a) => a.symbol) });
    this.chainsError.set(null);
  }

  protected removeChain(chain: ChainInterface): void {
    if (this.readonly()) {
      return;
    }
    this.order.set(this.order().filter((key) => key !== chain.key));
    const accepted = { ...this.accepted() };
    delete accepted[chain.key];
    this.accepted.set(accepted);
  }

  /** A network dragged within Accepted (a new order) or across the two lists. */
  protected dropChain(event: CdkDragDrop<string, string, ChainInterface>): void {
    const chain = event.item.data;
    if (event.previousContainer === event.container) {
      if (event.container.data === 'accepted') {
        const order = [...this.order()];
        moveItemInArray(order, event.previousIndex, event.currentIndex);
        this.order.set(order);
      }
      return;
    }
    if (event.container.data === 'accepted') {
      this.addChain(chain, event.currentIndex);
    } else {
      this.removeChain(chain);
    }
  }

  protected submit(): void {
    const values = this.products.controls.map((c) => String(c.value.value ?? '').trim());
    const duplicate = values.find((v, i) => v && values.indexOf(v) !== i);
    const empty = this.acceptedChains().find((c) => !(this.accepted()[c.key] ?? []).length);
    if (empty) {
      this.chainsError.set(`Choose at least one asset on ${empty.name}, or move it back to Available.`);
    }
    if (this.form.invalid || duplicate || empty) {
      revealErrors(this.form);
      if (duplicate) {
        this.products.controls[values.lastIndexOf(duplicate)].get('value')?.setErrors({ duplicate: true });
      }
      return;
    }
    const v = this.form.value;
    const text = (value: unknown) => (value === null || value === undefined ? '' : String(value).trim());
    const chains: Record<string, string[]> = {};
    for (const key of this.order()) {
      chains[key] = this.accepted()[key];
    }
    const locked = this.ownerFieldsLocked();
    this.save.emit({
      name: text(v.name),
      description: text(v.description) || null,
      website: text(v.website) || null,
      products: this.products.controls.map((c) => ({
        value: text(c.value.value),
        name: text(c.value.name),
        subname: text(c.value.subname) || null,
        price: text(c.value.price) || null
      })),
      // left out when locked: the server keeps them
      treasury: locked ? undefined : { evm: text(v.evm_treasury) || null, tron: text(v.tron_treasury) || null },
      chains,
      deposit_ttl_minutes: Number(v.deposit_ttl_minutes),
      remainder_window_minutes: Number(v.remainder_window_minutes),
      amount_tolerance_bps: Number(v.amount_tolerance_bps),
      allowed_ips: locked ? undefined : (v.allowed_ips as string[]).map((ip) => ip.trim()).filter((ip) => ip)
    });
  }
}
