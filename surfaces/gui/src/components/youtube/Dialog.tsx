import { useTranslation } from "react-i18next";
import { yt } from "./text";
import { type ReactNode, type RefObject } from "react";
import { Icon } from "../Icon";
import { ModalSurface, type ModalInput, useModalInteraction } from "../ModalSurface";

export function Dialog({
  open, interaction, fallbackFocusRef, title, subtitle, children, footer, onClose, detail = false, wide = false,
}: {
  open: boolean;
  interaction: ReturnType<typeof useModalInteraction>;
  fallbackFocusRef?: RefObject<HTMLElement>;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: (input?: ModalInput) => void;
  detail?: boolean;
  wide?: boolean;
}) {
  useTranslation();
  return (
    <ModalSurface
      open={open} interaction={interaction} fallbackFocusRef={fallbackFocusRef}
      label={title} surfaceClassName={`yp-dialog ${detail ? "yp-detail" : ""} ${wide ? "yp-dialog-wide" : ""}`}
      dismissOnBackdrop onRequestClose={() => onClose(interaction.closeInput.current)}
    >
      <header className="yp-dialoghead">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button className="yp-iconbtn" onClick={() => onClose()} aria-label={yt("关闭")}>
          <Icon name="x" />
        </button>
      </header>
      <div className="yp-dialogbody">{children}</div>
      {footer && <footer className="yp-dialogfoot">{footer}</footer>}
    </ModalSurface>
  );
}
