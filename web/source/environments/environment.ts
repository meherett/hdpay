export const environment = {
  production: false,
  // The API runs separately: python -m api in the repository root.
  endpoint: 'http://localhost:8090',
  // The customer checkout (HDPay web), for "open checkout" links.
  webUrl: 'http://localhost:4400'
};
