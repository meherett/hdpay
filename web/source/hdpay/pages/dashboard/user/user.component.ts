import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzMessageService } from 'ng-zorro-antd/message';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzPopconfirmModule } from 'ng-zorro-antd/popconfirm';
import { NzRadioModule } from 'ng-zorro-antd/radio';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';

import { errorMessage } from '../../../modules/error';
import { revealErrors } from '../../../modules/form';
import { ProjectInterface, Role, UserInterface } from '../../../services/api.interface';
import { AdminService } from '../../../services/admin/admin.service';
import { AuthService } from '../../../services/auth/auth.service';

/**
 * Admin users: owners also delete and manage owners, admins change things, viewers only look. An owner
 * gives each admin and viewer their projects: all of them, or some. There is no email: owners set
 * passwords here.
 */
@Component({
  selector: 'hdpay-user',
  imports: [
    DatePipe,
    FormsModule,
    ReactiveFormsModule,
    NzAlertModule,
    NzButtonModule,
    NzFormModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzPopconfirmModule,
    NzRadioModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule
  ],
  templateUrl: './user.component.html',
  styles: [`.role-select { width: 110px; } .project-select { width: 100%; margin-top: 12px; }`]
})
export class UserComponent {
  private readonly adminService = inject(AdminService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly message = inject(NzMessageService);
  protected readonly auth = inject(AuthService);

  protected readonly users = signal<UserInterface[]>([]);
  /** Every project, to choose from and to name them. */
  protected readonly projects = signal<ProjectInterface[]>([]);
  private readonly projectNames = computed(() => new Map(this.projects().map((p) => [p.id, p.name])));
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  // new user
  protected readonly creating = signal(false);
  protected readonly saving = signal(false);
  protected readonly createError = signal<string | null>(null);
  protected readonly createForm: FormGroup = this.formBuilder.group({
    name: [null, [Validators.required, Validators.maxLength(120)]],
    email: [null, [Validators.required, Validators.email]],
    role: ['viewer', [Validators.required]],
    password: [null, [Validators.required, Validators.minLength(8), Validators.maxLength(72)]],
    access: ['all'],      // all projects, or 'some'
    projects: [[]]        // with 'some': the ones they see
  });

  // a user's projects
  protected readonly accessFor = signal<UserInterface | null>(null);
  protected accessMode: 'all' | 'some' = 'all';
  protected accessProjects: string[] = [];

  // set password
  protected readonly passwordFor = signal<UserInterface | null>(null);
  protected readonly passwordError = signal<string | null>(null);
  protected readonly passwordForm: FormGroup = this.formBuilder.group({
    password: [null, [Validators.required, Validators.minLength(8), Validators.maxLength(72)]]
  });

  constructor() {
    inject(Title).setTitle('HDPay Admin - Users');
    this.load();
    this.adminService
      .projects()
      .subscribe({ next: (projects) => this.projects.set(projects.filter((p) => p.status !== 'locked')) });
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminService.users().subscribe({
      next: (users) => {
        this.users.set(users);
        this.loading.set(false);
      },
      error: (response: HttpErrorResponse) => {
        this.error.set(errorMessage(response));
        this.loading.set(false);
      }
    });
  }

  protected isMe(user: UserInterface): boolean {
    return user.id === this.auth.user()?.id;
  }

  protected openCreate(): void {
    this.createForm.reset({ role: 'viewer', access: 'all', projects: [] });
    this.createError.set(null);
    this.creating.set(true);
  }

  protected create(): void {
    this.createError.set(null);
    if (this.createForm.invalid) {
      revealErrors(this.createForm);
      return;
    }
    const { name, email, role, password, access, projects } = this.createForm.value;
    if (role !== 'owner' && access === 'some' && !projects.length) {
      this.createError.set('Choose at least one project, or give them all projects.');
      return;
    }
    this.saving.set(true);
    const scope = role === 'owner' || access === 'all' ? null : projects;
    this.adminService
      .createUser({ name: String(name).trim(), email: String(email).trim(), role, password, projects: scope })
      .subscribe({
        next: (user) => {
          this.saving.set(false);
          this.creating.set(false);
          this.message.success(`${user.email} can sign in now`);
          this.load();
        },
        error: (response: HttpErrorResponse) => {
          this.saving.set(false);
          this.createError.set(errorMessage(response));
        }
      });
  }

  /** The names of a user's projects; an id no longer known is shown as it is. */
  protected projectList(user: UserInterface): string[] {
    return (user.projects ?? []).map((id) => this.projectNames().get(id) ?? id);
  }

  protected openAccess(user: UserInterface): void {
    this.accessMode = user.projects ? 'some' : 'all';
    this.accessProjects = [...(user.projects ?? [])];
    this.accessFor.set(user);
  }

  protected saveAccess(): void {
    const user = this.accessFor();
    if (!user) {
      return;
    }
    if (this.accessMode === 'some' && !this.accessProjects.length) {
      this.message.error('Choose at least one project, or give them all projects.');
      return;
    }
    const projects = this.accessMode === 'all' ? 'all' : this.accessProjects;
    const done = projects === 'all' ? `${user.name} sees every project` : `${user.name} sees ${projects.length} project(s)`;
    this.accessFor.set(null);
    this.update(user, { projects }, done);
  }

  protected readonly roleColor: Record<Role, string> = { owner: 'gold', admin: 'geekblue', viewer: 'default' };

  /** An owner is managed only by an owner. */
  protected canManage(user: UserInterface): boolean {
    return this.auth.isAdmin() && (user.role !== 'owner' || this.auth.isOwner());
  }

  protected setRole(user: UserInterface, role: Role): void {
    const article = role === 'viewer' ? 'a' : 'an';
    this.update(user, { role }, `${user.name} is ${article} ${role} now`);
  }

  protected setActive(user: UserInterface, active: boolean): void {
    this.update(user, { is_active: active }, active ? `${user.name} can sign in again` : `${user.name} is disabled and signed out`);
  }

  private update(
    user: UserInterface,
    body: { role?: Role; is_active?: boolean; projects?: 'all' | string[] },
    done: string
  ): void {
    this.adminService.updateUser(user.id, body).subscribe({
      next: () => {
        this.message.success(done);
        this.load();
      },
      error: (response: HttpErrorResponse) => {
        this.message.error(errorMessage(response));
        this.load();
      }
    });
  }

  protected openPassword(user: UserInterface): void {
    this.passwordForm.reset();
    this.passwordError.set(null);
    this.passwordFor.set(user);
  }

  protected savePassword(): void {
    const user = this.passwordFor();
    if (!user) {
      return;
    }
    if (this.passwordForm.invalid) {
      revealErrors(this.passwordForm);
      return;
    }
    this.saving.set(true);
    this.adminService.setUserPassword(user.id, this.passwordForm.value.password).subscribe({
      next: () => {
        this.saving.set(false);
        this.passwordFor.set(null);
        this.message.success(`New password set for ${user.name}; their sessions ended`);
      },
      error: (response: HttpErrorResponse) => {
        this.saving.set(false);
        this.passwordError.set(errorMessage(response));
      }
    });
  }

  protected remove(user: UserInterface): void {
    this.adminService.deleteUser(user.id).subscribe({
      next: () => {
        this.message.success(`${user.email} deleted`);
        this.load();
      },
      error: (response: HttpErrorResponse) => this.message.error(errorMessage(response))
    });
  }
}
