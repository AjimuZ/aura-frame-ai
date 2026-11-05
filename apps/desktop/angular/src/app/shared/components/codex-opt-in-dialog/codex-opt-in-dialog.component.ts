// Consent dialog for the optional Codex provider of one library: it states what leaves the computer and how many
// calls a run may make, and reports the person's answer. It stores nothing; the parent saves the choice through the
// typed bridge.
import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  DOCUMENT,
  ElementRef,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';

// A native modal <dialog>: while it is open the rest of the window is inert, Tab stays inside it and Escape asks to
// close it. Focus starts on "Keep local only", the answer that sends nothing, and returns to the element that opened
// the dialog when the parent removes it. Both answers have the same visual weight, so allowing is never the easier
// click.
@Component({
  selector: 'af-codex-opt-in-dialog',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dialog
      #dialog
      class="af-consent"
      aria-labelledby="af-codex-opt-in-title"
      aria-describedby="af-codex-opt-in-summary"
      (cancel)="onCancel($event)"
      (close)="onClose()"
    >
      <div class="af-consent__body">
        <h2 id="af-codex-opt-in-title" class="af-consent__title">Allow Codex for {{ libraryName() }}?</h2>
        <p id="af-codex-opt-in-summary" class="af-consent__text">
          Codex is an optional cloud judge run by OpenAI. If you allow it, a run may ask it to judge photos from this
          library, and copies of those photos leave this computer.
        </p>

        <h3 class="af-consent__heading">What is sent</h3>
        <ul class="af-consent__list">
          <li>Only resized, EXIF-stripped, anonymous copies of the photos being judged.</li>
          <li>Never the originals, file names, folder paths or location.</li>
        </ul>

        <h3 class="af-consent__heading">How many calls</h3>
        <p class="af-consent__text">
          At most <strong>{{ maxCalls() }}</strong> Codex calls per run, retries included. Each call sends one or two
          copies. At the limit Codex is not asked again, and the run goes on with local scoring.
        </p>

        <h3 class="af-consent__heading">When copies are sent</h3>
        <p class="af-consent__text">
          Only when both are true: this library allows Codex, and the app was started with
          <code>./start.sh --provider codex --codex-opt-in</code>. Otherwise nothing is sent.
        </p>
        <p class="af-consent__text">
          Calls count against your own Codex plan or API usage, and your OpenAI account's data controls decide whether
          the copies may be used to improve models. You can turn this off in Settings at any time.
        </p>

        @if (error()) {
          <p class="af-consent__error" role="alert">{{ error() }}</p>
        }

        <div class="af-actions af-consent__actions">
          <button
            type="button"
            class="af-button-secondary"
            autofocus
            [attr.aria-disabled]="busy()"
            (click)="decline()"
          >
            Keep local only
          </button>
          <button type="button" class="af-button-secondary" [attr.aria-disabled]="busy()" (click)="allow()">
            {{ busy() ? 'Saving…' : 'Allow for this library' }}
          </button>
        </div>
      </div>
    </dialog>
  `,
  styles: `
    .af-consent {
      width: min(560px, calc(100vw - 32px));
      max-width: none;
      max-height: calc(100vh - 32px);
      padding: 0;
      overflow: hidden;
      background: var(--af-bg-surface);
      color: var(--af-text-soft);
      border: 1px solid color-mix(in srgb, var(--af-text-muted) 28%, transparent);
      border-radius: 2px;
      box-shadow: none;
    }

    .af-consent::backdrop {
      background: color-mix(in srgb, var(--af-bg-deep) 82%, transparent);
    }

    /* Stepped corner marks of a primary window, drawn on two corners only. */
    .af-consent::before,
    .af-consent::after {
      content: '';
      position: absolute;
      width: 8px;
      height: 8px;
      background: var(--af-primary-soft);
      clip-path: polygon(0 0, 100% 0, 100% 2px, 2px 2px, 2px 100%, 0 100%);
      pointer-events: none;
    }

    .af-consent::before {
      top: 3px;
      left: 3px;
    }

    .af-consent::after {
      right: 3px;
      bottom: 3px;
      transform: rotate(180deg);
    }

    .af-consent__body {
      max-height: calc(100vh - 34px);
      overflow-y: auto;
      padding: var(--af-space-lg);
    }

    .af-consent__title {
      margin: 0 0 var(--af-space-sm);
      font-size: 18px;
      color: var(--af-text-main);
    }

    .af-consent__heading {
      margin: var(--af-space-md) 0 var(--af-space-xs);
      font-size: 14px;
      font-weight: 700;
      color: var(--af-text-main);
    }

    .af-consent__text,
    .af-consent__list {
      margin: 0 0 var(--af-space-sm);
      font-size: 14px;
      line-height: 1.5;
    }

    .af-consent__list {
      padding-left: 20px;
    }

    .af-consent code {
      padding: 1px 4px;
      border-radius: 2px;
      background: var(--af-bg-deep);
      color: var(--af-text-main);
      font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .af-consent__error {
      margin: var(--af-space-md) 0 0;
      padding-left: var(--af-space-sm);
      border-left: 2px solid var(--af-warning);
      color: var(--af-warning);
      font-size: 14px;
    }

    .af-consent__actions {
      justify-content: flex-end;
      margin-top: var(--af-space-lg);
    }

    .af-consent__actions [aria-disabled='true'] {
      opacity: 0.55;
      cursor: progress;
    }
  `,
})
export class CodexOptInDialogComponent {
  // The library's display name. Interpolated, so Angular escapes it.
  readonly libraryName = input.required<string>();
  // The per-run call cap to state; the parent passes the window's mirror of the engine default.
  readonly maxCalls = input.required<number>();
  // True while the parent saves the answer: both buttons ignore clicks and Escape is held.
  readonly busy = input(false);
  // Why the last save failed, shown inside the dialog; null when there is nothing to report.
  readonly error = input<string | null>(null);

  // The person allowed Codex for this library. Emitted at most once per click, never while busy.
  readonly allowed = output<void>();
  // The person kept the library local: "Keep local only", Escape, or the browser closing the dialog itself.
  readonly dismissed = output<void>();

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  // The rendered <dialog>, kept once it is shown so that removal can close it; null before the first render.
  private shown: HTMLDialogElement | null = null;
  // The element that had focus when the dialog was created (normally the button that opened it).
  private readonly opener = inject(DOCUMENT).activeElement;
  // Set while the component closes its own dialog on removal, so that close is not reported as an answer.
  private removing = false;

  constructor() {
    afterNextRender(() => {
      const dialog = this.dialogRef().nativeElement;
      if (!dialog.open) dialog.showModal();
      this.shown = dialog;
    });
    // The parent removes the component to close it. Leave the top layer first, so the window is no longer inert,
    // then put focus back where it was.
    inject(DestroyRef).onDestroy(() => {
      this.removing = true;
      if (this.shown?.open) this.shown.close();
      if (this.opener instanceof HTMLElement && this.opener.isConnected) this.opener.focus();
    });
  }

  // Reports "allow" unless a save is already running.
  allow(): void {
    if (!this.busy()) this.allowed.emit();
  }

  // Reports "keep local only" unless a save is already running.
  decline(): void {
    if (!this.busy()) this.dismissed.emit();
  }

  // Escape: held while a save runs, otherwise the native dialog closes and onClose reports it.
  onCancel(event: Event): void {
    if (this.busy()) event.preventDefault();
  }

  // The browser closed the dialog itself (Escape, or a repeated Escape it does not let the page hold). The parent
  // must then remove the component, or an invisible dialog would stay behind.
  onClose(): void {
    if (!this.removing) this.dismissed.emit();
  }
}
