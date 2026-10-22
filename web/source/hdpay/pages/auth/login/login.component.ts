import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { Title } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzFormModule } from 'ng-zorro-antd/form';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';

import { environment } from '../../../../environments/environment';
import { errorMessage } from '../../../modules/error';
import { revealErrors } from '../../../modules/form';
import { AuthService } from '../../../services/auth/auth.service';

@Component({
  selector: 'hdpay-login',
  imports: [ReactiveFormsModule, NzAlertModule, NzButtonModule, NzCardModule, NzFormModule, NzIconModule, NzInputModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css'
})
export class LoginComponent {
  private readonly formBuilder = inject(FormBuilder);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);

  protected readonly formGroup: FormGroup;
  protected readonly loading = signal(false);
  protected readonly alert = signal<{ type: 'success' | 'info' | 'warning' | 'error'; message: string } | null>(null);

  constructor() {
    this.title.setTitle('HDPay Admin - Sign in');
    this.formGroup = this.formBuilder.group({
      email: [null, [Validators.required, Validators.email]],
      password: [null, [Validators.required]]
    });
  }

  protected login(): void {
    this.alert.set(null);
    if (this.formGroup.invalid) {
      revealErrors(this.formGroup);
      return;
    }

    this.loading.set(true);
    const { email, password } = this.formGroup.value;
    this.authService.login(String(email).trim(), String(password)).subscribe({
      next: () => {
        this.loading.set(false);
        void this.router.navigate(['/dashboard']);
      },
      error: (response: HttpErrorResponse) => {
        this.loading.set(false);
        let message: string;
        if (response.status === 0) {
          message = `Cannot reach the API at ${environment.endpoint}. Is python -m api running, and is this page open at an address listed in HDPAY_CORS_ORIGINS?`;
        } else if (response.status === 503) {
          message = 'Sign-in is off on the server: set HDPAY_JWT_SECRET in its .env and restart it.';
        } else {
          // wrong email or password, a disabled account, or too many attempts: the API says which
          message = errorMessage(response);
        }
        this.alert.set({ type: 'error', message });
      }
    });
  }
}
