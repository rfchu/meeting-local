import { useEffect, useRef, useState } from "react";

type Action = {
  label: string;
  run: () => void;
  danger?: boolean;
};

export default function ActionMenu({
  label,
  accessibleLabel,
  actions,
}: {
  label: string;
  accessibleLabel?: string;
  actions: Action[];
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const trigger = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);

  function close(restoreFocus = false) {
    if (menu.current) menu.current.open = false;
    if (restoreFocus) trigger.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    const dismissOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target))
        close();
    };
    document.addEventListener("pointerdown", dismissOutside, true);
    return () =>
      document.removeEventListener("pointerdown", dismissOutside, true);
  }, [open]);

  return (
    <details
      className="action-menu"
      ref={menu}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      onBlur={(event) => {
        if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget))
          close();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && event.currentTarget.open) {
          event.preventDefault();
          event.stopPropagation();
          close(true);
        }
      }}
    >
      <summary ref={trigger} aria-label={accessibleLabel}>{label}</summary>
      <div className="action-menu-dropdown">
        {actions.map(({ label, run, danger }) => (
          <button
            key={label}
            type="button"
            className={danger ? "danger-text" : undefined}
            onClick={() => {
              close(true);
              run();
            }}
          >
            {label}
          </button>
        ))}
      </div>
    </details>
  );
}
