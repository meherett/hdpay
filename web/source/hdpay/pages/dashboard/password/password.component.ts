import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { AbstractControl, FormBuilder, FormGroup, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzMessageService } from 'ng-zorro-antd/message';

import { errorMessage } from '../../../modules/error';
import { revealErrors } from '../../../modules/form';
import { AuthService } from '../../../services/auth/auth.service';

function sameAsNew(control: AbstractControl): ValidationErrors | null {
  const form = control.parent;
  return form && control.value !== form.get('new_password')?.value ? { different: true } : null;
}

/** Change your own password. Every session ends, this one included, so you sign in again. */
@Component({
  selector: 'hdpay-password',
  imports: [ReactiveFormsModule, NzAlertModule, NzButtonModule, NzCardModule, NzFormModule, NzInputModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Change password</h1>
        <p>Every session you have ends, this one included, so you sign in again with the new password.</p>
      </div>
    </div>

    <nz-card class="password-card">
      @if (error(); as message) {
        <nz-alert class="block" nzType="error" nzShowIcon [nzMessage]="message" />
      }
      <form nz-form [formGroup]="form" nzLayout="vertical" (ngSubmit)="save()">
        <nz-form-item>
          <nz-form-label nzFor="old_password">Current password</nz-form-label>
          <nz-form-control nzErrorTip="Current password is required">
            <nz-input-password>
              <input nz-input id="old_password" type="password" formControlName="old_password" autocomplete="current-password" />
            </nz-input-password>
          </nz-form-control>
        </nz-form-item>
        <nz-form-item>
          <nz-form-label nzFor="new_password">New password</nz-form-label>
          <nz-form-control nzErrorTip="Use 8 to 72 characters">
            <nz-input-password>
              <input nz-input id="new_password" type="password" formControlName="new_password" autocomplete="new-password" />
            </nz-input-password>
          </nz-form-control>
        </nz-form-item>
        <nz-form-item>
          <nz-form-label nzFor="again">New password again</nz-form-label>
          <nz-form-control nzErrorTip="The passwords differ">
            <nz-input-password>
              <input nz-input id="again" type="password" formControlName="again" autocomplete="new-password" />
            </nz-input-password>
          </nz-form-control>
        </nz-form-item>
        <button nz-button nzType="primary" type="submit" [nzLoading]="saving()">Change password</button>
      </form>
    </nz-card>
  `,
  styles: [`.password-card { max-width: 480px; }`]
})
export class PasswordComponent {
  private readonly authService = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly message = inject(NzMessageService);
  private readonly router = inject(Router);

  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly form: FormGroup = this.formBuilder.group({
    old_password: [null, [Validators.required]],
    new_password: [null, [Validators.required, Validators.minLength(8), Validators.maxLength(72)]],
    again: [null, [Validators.required, sameAsNew]]
  });

  constructor() {
    inject(Title).setTitle('HDPay Admin - Change password');
  }

  protected save(): void {
    this.error.set(null);
    this.form.controls['again'].updateValueAndValidity();
    if (this.form.invalid) {
      revealErrors(this.form);
      return;
    }
    this.saving.set(true);
    this.authService.changePassword(this.form.value.old_password, this.form.value.new_password).subscribe({
      next: () => {
        this.saving.set(false);
        this.authService.clearSession();
        this.message.success('Password changed. Sign in with the new one.');
        void this.router.navigate(['/login']);
      },
      error: (response: HttpErrorResponse) => {
        this.saving.set(false);
        this.error.set(errorMessage(response));
      }
    });
  }
}
