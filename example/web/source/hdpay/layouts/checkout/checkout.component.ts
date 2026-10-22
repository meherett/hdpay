import { Component } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';

@Component({
  selector: 'hdpay-checkout',
  imports: [RouterLink, RouterOutlet],
  templateUrl: './checkout.component.html',
  styleUrl: './checkout.component.css'
})
export class CheckoutComponent {
  protected readonly year = new Date().getFullYear();
}
