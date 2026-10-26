import { provideHttpClient } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import {
  ArrowRightOutline,
  CheckCircleOutline,
  ClockCircleFill,
  ClockCircleOutline,
  CloseCircleOutline,
  CopyOutline,
  ExportOutline,
  InfoCircleOutline,
  MailOutline,
  NodeIndexOutline,
  SafetyOutline,
  ReloadOutline,
  SearchOutline,
  SwapOutline,
  UserOutline,
  WalletOutline,
  WarningOutline
} from '@ant-design/icons-angular/icons';
import { en_US, provideNzI18n } from 'ng-zorro-antd/i18n';
import { provideNzIcons } from 'ng-zorro-antd/icon';

import { routes } from './hdpay.routes';

const icons = [
  ArrowRightOutline,
  CheckCircleOutline,
  ClockCircleFill,
  ClockCircleOutline,
  CloseCircleOutline,
  CopyOutline,
  ExportOutline,
  InfoCircleOutline,
  MailOutline,
  NodeIndexOutline,
  SafetyOutline,
  ReloadOutline,
  SearchOutline,
  SwapOutline,
  UserOutline,
  WalletOutline,
  WarningOutline
];

export const hdpayConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(),
    provideAnimationsAsync(),
    provideNzI18n(en_US),
    provideNzIcons(icons)
  ]
};
