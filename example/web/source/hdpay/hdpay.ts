import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

@Component({
  imports: [RouterOutlet],
  selector: 'hdpay-root',
  styleUrl: './hdpay.css',
  template: '<router-outlet />'
})
export class Hdpay {}
