import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';

import { DeleteProjectComponent } from '../../../components/delete-project/delete-project.component';
import { errorMessage } from '../../../modules/error';
import { PROJECT_STATUS } from '../../../modules/status';
import { ProjectInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';
import { AuthService } from '../../../services/auth/auth.service';

/** Every project: the apps that take payments through HDPay. */
@Component({
  selector: 'hdpay-project',
  imports: [
    DatePipe,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule,
    DeleteProjectComponent
  ],
  templateUrl: './project.component.html'
})
export class ProjectComponent {
  private readonly adminService = inject(AdminService);
  protected readonly auth = inject(AuthService);

  protected readonly projects = signal<ProjectInterface[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly projectStatus = PROJECT_STATUS;
  /** The project in the delete dialog, null while it is closed. */
  protected readonly deleteTarget = signal<ProjectInterface | null>(null);

  constructor() {
    inject(Title).setTitle('HDPay Admin - Projects');
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminService.projects().subscribe({
      next: (projects) => {
        this.projects.set(projects);
        this.loading.set(false);
      },
      error: (response: HttpErrorResponse) => {
        this.error.set(errorMessage(response));
        this.loading.set(false);
      }
    });
  }

  protected onDeleted(): void {
    this.deleteTarget.set(null);
    this.load();
  }

  protected networks(project: ProjectInterface): string {
    const keys = Object.keys(project.chains);
    return keys.length ? keys.join(', ') : 'none yet';
  }
}
