import { Component, inject } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';

import { ThemeService } from '../../services/theme/theme.service';

@Component({
  selector: 'hdpay-passport',
  imports: [RouterLink, RouterOutlet, NzButtonModule, NzIconModule],
  templateUrl: './passport.component.html',
  styleUrl: './passport.component.css'
})
export class PassportComponent {
  protected readonly year = new Date().getFullYear();
  protected readonly theme = inject(ThemeService);
}
