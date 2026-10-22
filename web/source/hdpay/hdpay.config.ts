import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import {
  AppstoreOutline,
  CheckOutline,
  CloseOutline,
  CopyOutline,
  DashboardOutline,
  DeleteOutline,
  ExclamationCircleOutline,
  ExportOutline,
  EyeOutline,
  GlobalOutline,
  HolderOutline,
  KeyOutline,
  LockOutline,
  LogoutOutline,
  MinusCircleOutline,
  MoonOutline,
  PlusOutline,
  RedoOutline,
  ReloadOutline,
  SearchOutline,
  StopOutline,
  SunOutline,
  SwapOutline,
  TeamOutline,
  TransactionOutline,
  UnlockOutline,
  UserOutline,
  WarningOutline
} from '@ant-design/icons-angular/icons';
import { en_US, provideNzI18n } from 'ng-zorro-antd/i18n';
import { provideNzIcons } from 'ng-zorro-antd/icon';

import { authInterceptor } from './modules/auth/auth.interceptor';
import { routes } from './hdpay.routes';

const icons = [
  AppstoreOutline,
  CheckOutline,
  CloseOutline,
  CopyOutline,
  DashboardOutline,
  DeleteOutline,
  ExclamationCircleOutline,
  ExportOutline,
  EyeOutline,
  GlobalOutline,
  HolderOutline,
  KeyOutline,
  LockOutline,
  LogoutOutline,
  MinusCircleOutline,
  MoonOutline,
  PlusOutline,
  RedoOutline,
  ReloadOutline,
  SearchOutline,
  StopOutline,
  SunOutline,
  SwapOutline,
  TeamOutline,
  TransactionOutline,
  UnlockOutline,
  UserOutline,
  WarningOutline
];

export const hdpayConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideAnimationsAsync(),
    provideNzI18n(en_US),
    provideNzIcons(icons)
  ]
};
