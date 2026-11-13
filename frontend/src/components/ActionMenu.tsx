import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  firstEnabledMenuIndex,
  lastEnabledMenuIndex,
  nextEnabledMenuIndex,
} from "./menuNavigation";

export { firstEnabledMenuIndex, lastEnabledMenuIndex, nextEnabledMenuIndex };

type Action = {
  label: string;
  icon?: ReactNode;
  run: () => void;
  danger?: boolean;
  disabled?: boolean;
};

export default function ActionMenu({
  label,
  icon,
  accessibleLabel,
  actions,
}: {
  label: string;
  icon?: ReactNode;
  accessibleLabel?: string;
  actions: Action[];
}) {
  const [open, setOpen] = useState(false);
  const pendingFocusRef = useRef<"first" | "last" | "container" | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const getMenuItems = useCallback(() => {
    if (!menuRef.current) return [];
    return Array.from(
      menuRef.current.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'),
    );
  }, []);

  const focusByIndex = useCallback(
    (index: number) => {
      const items = getMenuItems();
      if (index >= 0 && index < items.length && !items[index].disabled) {
        items[index].focus();
      }
    },
    [getMenuItems],
  );

  useEffect(() => {
    if (!open) return;

    const target = pendingFocusRef.current;
    pendingFocusRef.current = null;
    if (target === "first" || target === "last") {
      const flags = getMenuItems().map((item) => item.disabled);
      const index =
        target === "first"
          ? firstEnabledMenuIndex(flags)
          : lastEnabledMenuIndex(flags);
      if (index >= 0) {
        focusByIndex(index);
      } else {
        menuRef.current?.focus();
      }
    } else if (target === "container") {
      menuRef.current?.focus();
    }

    const handleOutside = (event: MouseEvent | TouchEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };

    const handleWindowKeyDown = (event: KeyboardEvent) => {
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
    window.addEventListener("keydown", handleWindowKeyDown, true);

    return () => {
      document.removeEventListener("mousedown", handleOutside, true);
      document.removeEventListener("pointerdown", handleOutside, true);
      document.removeEventListener("touchstart", handleOutside, true);
      window.removeEventListener("keydown", handleWindowKeyDown, true);
    };
  }, [open, focusByIndex, getMenuItems]);

  const handleTriggerKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
  ) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      pendingFocusRef.current = "first";
      setOpen(true);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      pendingFocusRef.current = "last";
      setOpen(true);
    }
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = getMenuItems();
    const disabledFlags = items.map((item) => item.disabled);
    const activeIndex = items.findIndex(
      (item) => item === document.activeElement,
    );

    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusByIndex(nextEnabledMenuIndex(disabledFlags, activeIndex, 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusByIndex(nextEnabledMenuIndex(disabledFlags, activeIndex, -1));
    } else if (event.key === "Home") {
      event.preventDefault();
      focusByIndex(firstEnabledMenuIndex(disabledFlags));
    } else if (event.key === "End") {
      event.preventDefault();
      focusByIndex(lastEnabledMenuIndex(disabledFlags));
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

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
        onKeyDown={handleTriggerKeyDown}
        onClick={(event) => {
          if (open) {
            setOpen(false);
            return;
          }
          pendingFocusRef.current = event.detail === 0 ? "first" : "container";
          setOpen(true);
        }}
      >
        {icon && <span className="action-menu-icon">{icon}</span>}
        <span>{label}</span>
      </button>

      {open && (
        <div
          ref={menuRef}
          className="action-menu-dropdown"
          role="menu"
          tabIndex={-1}
          onKeyDown={handleMenuKeyDown}
        >
          {actions.map(
            ({
              label: actionLabel,
              icon: actionIcon,
              run,
              danger,
              disabled,
            }) => (
              <button
                key={actionLabel}
                type="button"
                role="menuitem"
                disabled={disabled}
                className={danger ? "danger-text" : undefined}
                onClick={() => {
                  if (disabled) return;
                  setOpen(false);
                  triggerRef.current?.focus();
                  run();
                }}
              >
                {actionIcon && (
                  <span className="action-menu-icon">{actionIcon}</span>
                )}
                <span>{actionLabel}</span>
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
