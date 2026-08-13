"use client";

import { AlertCircle, LockKeyhole, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, ButtonLoading } from "@/components/ui/button";
import type { DashboardTray } from "@/lib/dashboard";
import { overrideRfidMapping, unlinkRfidMapping } from "./actions";

type Props = {
  tray: DashboardTray;
  onClose: () => void;
};

export function RfidMappingDialog({ tray, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<
    "override" | "unlink" | null
  >(null);
  const [isPending, startTransition] = useTransition();
  const router = useRouter();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  function runAction(action: "override" | "unlink") {
    if (!tray.spool || !tray.rfidTag) return;
    const spoolId = tray.spool.id;
    const rfidTag = tray.rfidTag;

    setError(null);
    setPendingAction(action);
    startTransition(async () => {
      const result =
        action === "override"
          ? await overrideRfidMapping(tray.id, rfidTag)
          : await unlinkRfidMapping(tray.id, spoolId, rfidTag);

      if (result.error) {
        setError(result.error);
        setPendingAction(null);
        return;
      }

      dialogRef.current?.close();
      onClose();
      router.refresh();
    });
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="rfid-dialog-title"
      className="m-auto max-h-[calc(100dvh_-_1rem)] w-[calc(100%_-_1rem)] max-w-lg overflow-hidden rounded-2xl border bg-card p-0 text-card-foreground shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-xs sm:w-[calc(100%_-_2rem)]"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <div className="flex max-h-[calc(100dvh_-_1rem)] flex-col">
        <div className="min-h-0 overflow-y-auto p-5 sm:p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="grid size-11 place-items-center rounded-xl bg-primary/12 text-primary">
              <LockKeyhole className="size-5" />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-11 sm:size-9"
              aria-label="Close RFID mapping dialog"
              onClick={onClose}
            >
              <X />
            </Button>
          </div>

          <h2
            id="rfid-dialog-title"
            className="mt-4 text-xl font-semibold tracking-tight"
          >
            RFID controls this mapping
          </h2>
          <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
            AMS {tray.amsNumber} · Tray {tray.trayNumber} is automatically mapped
            to spool #{tray.spool?.id} whenever this tag is detected.
          </p>

          <div className="mt-5 rounded-xl bg-muted/60 p-3.5 text-sm">
            Linked to{" "}
            <strong>
              spool #{tray.spool?.id} · {tray.spool?.name}
            </strong>
            <code className="mt-1.5 block overflow-hidden text-ellipsis whitespace-nowrap text-xs text-muted-foreground">
              RFID {tray.rfidTag}
            </code>
          </div>

          <div className="mt-4 rounded-xl border border-destructive/35 bg-destructive/10 p-3.5">
            <strong className="text-sm">Remove the RFID association</strong>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Future insertions of this tag will no longer select spool #
              {tray.spool?.id} automatically.
            </p>
            <Button
              type="button"
              variant="destructive"
              className="mt-3 h-11 sm:h-9"
              disabled={isPending}
              onClick={() => runAction("unlink")}
            >
              <ButtonLoading
                loading={pendingAction === "unlink" && isPending}
              />
              Unlink permanently
            </Button>
          </div>

          {error && (
            <Alert variant="destructive" className="mt-4">
              <AlertCircle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <div className="flex shrink-0 flex-col-reverse justify-end gap-2 border-t bg-muted/35 px-5 py-4 sm:flex-row sm:px-6">
          <Button
            type="button"
            variant="outline"
            className="h-11 sm:h-9"
            disabled={isPending}
            onClick={onClose}
          >
            Keep automatic mapping
          </Button>
          <Button
            type="button"
            className="h-11 sm:h-9"
            disabled={isPending}
            onClick={() => runAction("override")}
          >
            <ButtonLoading
              loading={pendingAction === "override" && isPending}
            />
            Override until spool is removed
          </Button>
        </div>
      </div>
    </dialog>
  );
}
