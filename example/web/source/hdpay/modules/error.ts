import { HttpErrorResponse } from '@angular/common/http';

/**
 * Turns any failed response into one sentence for the customer.
 *
 * The API answers with {"detail": "..."} for single errors and
 * {"detail": [{loc, msg, type}, ...]} for 422 field validation.
 */
export function errorMessage(response: HttpErrorResponse): string {
  if (response.status === 0) {
    return 'Please check your connection and try again.';
  }

  if (response.status >= 500) {
    return 'Something went wrong on our side. Try again later.';
  }

  const detail = response.error?.detail;

  if (typeof detail === 'string') {
    return detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item: { loc?: (string | number)[]; msg?: string }) => {
        const field = item.loc?.filter((part) => part !== 'body').join('.');
        const message = (item.msg ?? '').replace(/^Value error, /, '');
        return field ? `${field}: ${message}` : message;
      })
      .join(' ');
  }

  return 'Request failed. Please try again.';
}
