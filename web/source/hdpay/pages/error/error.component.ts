import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzResultModule } from 'ng-zorro-antd/result';

@Component({
  selector: 'hdpay-error',
  imports: [RouterLink, NzButtonModule, NzResultModule],
  template: `
    <nz-result nzStatus="404" nzTitle="404" nzSubTitle="This page does not exist.">
      <div nz-result-extra>
        <a nz-button nzType="primary" routerLink="/">Back home</a>
      </div>
    </nz-result>
  `,
  styles: [':host { display: flex; align-items: center; justify-content: center; min-height: 100vh; }']
})
export class ErrorComponent {}
