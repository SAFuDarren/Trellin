// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";

type Props = {
  message: string;
  action?: { label: string; disabled: boolean; run: () => void };
  onDismiss: () => void;
};

export function ActionFeedback({ message, action, onDismiss }: Props) {
  const { t } = useTranslation();
  return (
    <div className="workbench-action-toast">
      <span>{message}</span>
      {action && (
        <button type="button" className="conversation-text-action" disabled={action.disabled} onClick={action.run}>
          {action.label}
        </button>
      )}
      <button type="button" className="conversation-text-action" onClick={onDismiss}>
        {t("graphActions.dismiss")}
      </button>
    </div>
  );
}
