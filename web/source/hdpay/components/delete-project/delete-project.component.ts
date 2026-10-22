import { HttpErrorResponse } from '@angular/common/http';
import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzModalModule } from 'ng-zorro-antd/modal';

import { errorMessage } from '../../modules/error';
import { ProjectInterface } from '../../services/api.interface';
import { AdminService } from '../../services/admin/admin.service';

/**
 * Delete a project, for owners: its API keys, deposit addresses and events go with it, so the owner
 * types the project's name to confirm. Open it by setting `project`; it emits `deleted` or `closed`.
 */
@Component({
  selector: 'hdpay-delete-project',
  imports: [NzInputModule, NzModalModule],
  templateUrl: './delete-project.component.html'
})
export class DeleteProjectComponent {
  private readonly adminService = inject(AdminService);
  private readonly message = inject(NzMessageService);

  readonly project = input<ProjectInterface | null>(null);
  readonly closed = output<void>();
  readonly deleted = output<ProjectInterface>();

  protected readonly name = signal('');
  protected readonly deleting = signal(false);

  constructor() {
    // each time it opens, the name starts empty
    effect(() => {
      this.project();
      untracked(() => this.name.set(''));
    });
  }

  protected remove(): void {
    const project = this.project();
    if (!project || this.name() !== project.name) {
      return;
    }
    this.deleting.set(true);
    this.adminService.deleteProject(project.id, this.name()).subscribe({
      next: () => {
        this.deleting.set(false);
        this.message.success(`${project.name} deleted`);
        this.deleted.emit(project);
      },
      error: (response: HttpErrorResponse) => {
        this.deleting.set(false);
        this.message.error(errorMessage(response));
      }
    });
  }
}
