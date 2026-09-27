import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  onClose: () => void;
  id?: string;
  className?: string;
}

export default function Modal({ children, onClose, id, className }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      if (dialog?.open) dialog.close();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      id={id}
      className={className}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      {children}
    </dialog>
  );
}
