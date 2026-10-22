import { bootstrapApplication } from '@angular/platform-browser';
import { hdpayConfig } from './hdpay/hdpay.config';
import { Hdpay } from './hdpay/hdpay';

bootstrapApplication(Hdpay, hdpayConfig)
  .catch((err) => console.error(err));
