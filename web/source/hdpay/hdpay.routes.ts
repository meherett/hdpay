import { Routes } from '@angular/router';

import { adminGuard, ownerGuard, publicGuard } from './modules/auth/auth.guard';

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'dashboard',
    pathMatch: 'full'
  },
  {
    path: '',
    canActivate: [publicGuard],
    loadComponent: () =>
      import('./layouts/passport/passport.component').then((m) => m.PassportComponent),
    children: [
      {
        path: 'login',
        loadComponent: () => import('./pages/auth/login/login.component').then((m) => m.LoginComponent)
      }
    ]
  },
  {
    path: 'dashboard',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./layouts/dashboard/dashboard.component').then((m) => m.DashboardComponent),
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./pages/dashboard/overview/overview.component').then((m) => m.OverviewComponent),
        pathMatch: 'full'
      },
      {
        path: 'deposit',
        loadComponent: () =>
          import('./pages/dashboard/deposit/deposit.component').then((m) => m.DepositComponent)
      },
      {
        path: 'watcher',
        loadComponent: () =>
          import('./pages/dashboard/watcher/watcher.component').then((m) => m.WatcherComponent)
      },
      {
        path: 'sweeper',
        canActivate: [ownerGuard],
        loadComponent: () =>
          import('./pages/dashboard/sweeper/sweeper.component').then((m) => m.SweeperComponent)
      },
      {
        path: 'project',
        loadComponent: () =>
          import('./pages/dashboard/project/project.component').then((m) => m.ProjectComponent)
      },
      {
        path: 'project/:id',
        loadComponent: () =>
          import('./pages/dashboard/project/detail/project-detail.component').then((m) => m.ProjectDetailComponent)
      },
      {
        path: 'user',
        canActivate: [ownerGuard],
        loadComponent: () => import('./pages/dashboard/user/user.component').then((m) => m.UserComponent)
      },
      {
        path: 'chain',
        loadComponent: () => import('./pages/dashboard/chain/chain.component').then((m) => m.ChainComponent)
      },
      {
        path: 'password',
        loadComponent: () =>
          import('./pages/dashboard/password/password.component').then((m) => m.PasswordComponent)
      },
      {
        path: 'deposit/:id',
        loadComponent: () =>
          import('./pages/dashboard/deposit/detail/detail.component').then((m) => m.DetailComponent)
      }
    ]
  },
  {
    path: '**',
    loadComponent: () => import('./pages/error/error.component').then((m) => m.ErrorComponent)
  }
];
