import { useEffect, useRef, useState } from "react";

type Action = {
  label: string;
  run: () => void;
  danger?: boolean;
  disabled?: boolean;
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
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    const handleOutside = (event: MouseEvent | TouchEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener("mousedown", handleOutside, true);
    document.addEventListener("pointerdown", handleOutside, true);
    document.addEventListener("touchstart", handleOutside, true);
    window.addEventListener("keydown", handleKeyDown, true);

    return () => {
      document.removeEventListener("mousedown", handleOutside, true);
      document.removeEventListener("pointerdown", handleOutside, true);
      document.removeEventListener("touchstart", handleOutside, true);
      window.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  return (
    <div
      className={`action-menu ${open ? "open" : ""}`}
      ref={containerRef}
      onBlur={(event) => {
        if (!containerRef.current?.contains(event.relatedTarget as Node)) {
          setOpen(false);
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className="action-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={accessibleLabel}
        onClick={() => setOpen((prev) => !prev)}
      >
        {label}
      </button>

      {open && (
        <div className="action-menu-dropdown" role="menu">
          {actions.map(({ label: actionLabel, run, danger, disabled }) => (
            <button
              key={actionLabel}
              type="button"
              role="menuitem"
              disabled={disabled}
              className={danger ? "danger-text" : undefined}
              onClick={() => {
                if (disabled) return;
                setOpen(false);
                run();
              }}
            >
              {actionLabel}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
