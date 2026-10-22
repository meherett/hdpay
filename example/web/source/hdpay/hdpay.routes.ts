import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./pages/home/home.component').then((m) => m.HomeComponent),
    pathMatch: 'full'
  },
  {
    path: '',
    loadComponent: () =>
      import('./layouts/checkout/checkout.component').then((m) => m.CheckoutComponent),
    children: [
      {
        path: 'pay/:id',
        loadComponent: () => import('./pages/pay/pay.component').then((m) => m.PayComponent)
      }
    ]
  },
  {
    path: '**',
    loadComponent: () => import('./pages/error/error.component').then((m) => m.ErrorComponent)
  }
];
