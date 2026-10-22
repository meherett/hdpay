import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzLayoutModule } from 'ng-zorro-antd/layout';
import { NzMenuModule } from 'ng-zorro-antd/menu';

import { AuthService } from '../../services/auth/auth.service';
import { ThemeService } from '../../services/theme/theme.service';

/** The signed-in shell: the brand, the pages and the account across the top, the page below. */
@Component({
  selector: 'hdpay-dashboard-layout',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzButtonModule, NzIconModule, NzLayoutModule, NzMenuModule],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.css'
})
export class DashboardComponent {
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  protected readonly theme = inject(ThemeService);

  protected logout(): void {
    this.auth.logout();
    void this.router.navigate(['/login']);
  }
}
