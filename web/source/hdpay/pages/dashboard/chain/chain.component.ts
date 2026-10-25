import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzSwitchModule } from 'ng-zorro-antd/switch';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';

import { errorMessage } from '../../../modules/error';
import { NetworkAssetInterface, NetworkInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';

/**
 * The networks and coins of chains.yaml as the database keeps them: those supported now, and those
 * accepted before and taken out of the file since, which stay for their deposits.
 */
@Component({
  selector: 'hdpay-chain',
  imports: [
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzCardModule,
    NzIconModule,
    NzSkeletonModule,
    NzSwitchModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule
  ],
  templateUrl: './chain.component.html',
  styleUrl: './chain.component.css'
})
export class ChainComponent {
  private readonly adminService = inject(AdminService);
  private readonly title = inject(Title);

  protected readonly networks = signal<NetworkInterface[] | null>(null);
  protected readonly error = signal<string | null>(null);
  /** Show the networks and coins no longer supported too. */
  protected readonly showAll = signal(true);

  protected readonly shown = computed(() =>
    (this.networks() ?? [])
      .filter((n) => this.showAll() || n.supported)
      .map((n) => ({ ...n, assets: n.assets.filter((a) => this.showAll() || a.supported) }))
  );
  protected readonly counts = computed(() => {
    const assets = (this.networks() ?? []).flatMap((n) => n.assets);
    return {
      supported: assets.filter((a) => a.status === 'supported').length,
      unsupported: assets.filter((a) => a.status === 'not supported').length,
      problems: assets.filter((a) => a.status === 'refused' || a.status === 'unchecked').length
    };
  });

  constructor() {
    this.title.setTitle('HDPay Admin - Chains');
    this.adminService.networks().subscribe({
      next: (networks) => this.networks.set(networks),
      error: (response: HttpErrorResponse) => this.error.set(errorMessage(response))
    });
  }

  protected color(asset: NetworkAssetInterface): string {
    return { supported: 'success', 'not supported': 'default', refused: 'error', unchecked: 'warning' }[asset.status];
  }

  protected contractUrl(network: NetworkInterface, contract: string): string {
    return network.family === 'tron' ? `${network.explorer}/#/token20/${contract}` : `${network.explorer}/token/${contract}`;
  }
}
