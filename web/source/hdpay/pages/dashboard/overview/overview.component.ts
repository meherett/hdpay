import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzSkeletonModule } from 'ng-zorro-antd/skeleton';
import { NzStatisticModule } from 'ng-zorro-antd/statistic';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';

import { formatUnits } from '../../../modules/amount';
import { errorMessage } from '../../../modules/error';
import { live } from '../../../modules/live';
import { STATUS, STATUSES } from '../../../modules/status';
import { ProjectInterface, StatsInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';

/** Deposits by status and what was credited, for every project or one. Balances are on each project's page. */
@Component({
  selector: 'hdpay-overview',
  imports: [
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzSelectModule,
    NzSkeletonModule,
    NzStatisticModule,
    NzTableModule,
    NzTagModule
  ],
  templateUrl: './overview.component.html',
  styleUrl: './overview.component.css'
})
export class OverviewComponent {
  private readonly adminService = inject(AdminService);

  protected readonly stats = signal<StatsInterface | null>(null);
  protected readonly projects = signal<ProjectInterface[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly statuses = STATUSES;
  protected readonly status = STATUS;
  protected readonly formatUnits = formatUnits;
  protected projectId: string | null = null;

  constructor() {
    inject(Title).setTitle('HDPay Admin - Overview');
    this.adminService
      .projects()
      .subscribe({ next: (projects) => this.projects.set(projects.filter((p) => p.status !== 'locked')) });
    this.load();
    live(() => this.load(true), inject(DestroyRef));
  }

  /** `quiet`: a background reload, without the spinner. */
  protected load(quiet = false): void {
    if (!quiet) {
      this.loading.set(true);
      this.error.set(null);
    }
    this.adminService.stats(this.projectId).subscribe({
      next: (stats) => {
        this.stats.set(stats);
        this.error.set(null);
        this.loading.set(false);
      },
      error: (response: HttpErrorResponse) => {
        this.error.set(errorMessage(response));
        this.loading.set(false);
      }
    });
  }

  /** Query params for a status card's link: the status, and the project when one is chosen. */
  protected depositsFilter(status: string): Record<string, string> {
    return this.projectId ? { status, project_id: this.projectId } : { status };
  }
}
