import { FormGroup } from '@angular/forms';

/**
 * Shows every field's error at once. ng-zorro only colours a field and shows its error tip
 * once the control is dirty, so touched alone leaves an empty, untouched form silent.
 */
export function revealErrors(form: FormGroup): void {
  for (const control of Object.values(form.controls)) {
    control.markAsDirty();
    control.markAsTouched();
    control.updateValueAndValidity({ onlySelf: true });
  }
}
