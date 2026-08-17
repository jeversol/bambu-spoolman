"use client";

import { type IDetectedBarcode, Scanner } from "@yudiel/react-qr-scanner";
import { AlertCircle, ScanLine, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  type UpdateTrayAssignmentActionData,
  updateTrayAssignment,
} from "@/components/tray-config/actions";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, ButtonLoading } from "@/components/ui/button";
import type { DashboardSpool, DashboardTray } from "@/lib/dashboard";
import { useCameraAvailable } from "@/lib/hooks/useCameraAvailable";
import { getSpoolIdFromQrValue } from "@/lib/qr";
import { cn } from "@/lib/utils";

export type AssignmentTarget = Pick<
  DashboardTray,
  | "id"
  | "amsNumber"
  | "trayNumber"
  | "occupied"
  | "printerName"
  | "printerColorHex"
  | "spool"
> & {
  external?: boolean;
};

type Props = {
  target: AssignmentTarget;
  spools: DashboardSpool[];
  assignments: Record<number, string>;
  onClose: () => void;
};

export function SpoolAssignmentDialog({
  target,
  spools,
  assignments,
  onClose,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const scanHandledRef = useRef(false);
  const [selectedSpool, setSelectedSpool] = useState(
    target.spool?.id.toString() ?? "",
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [qrScanning, setQrScanning] = useState(false);
  const [qrScanError, setQrScanError] = useState<string | null>(null);
  const [actionState, setActionState] =
    useState<UpdateTrayAssignmentActionData>({ error: null });
  const [pendingAction, setPendingAction] = useState<
    "assign" | "clear" | null
  >(null);
  const [isPending, startTransition] = useTransition();
  const cameraAvailable = useCameraAvailable();
  const router = useRouter();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const filteredSpools = useMemo(() => {
    const terms = searchQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return spools;

    return spools.filter((spool) => {
      const searchable = [
        spool.id,
        spool.name,
        spool.vendor,
        spool.material,
        spool.colorHex,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return terms.every((term) => searchable.includes(term));
    });
  }, [searchQuery, spools]);

  const currentSpoolId = target.spool?.id.toString() ?? "";
  const targetId = target.id;
  const location = target.external
    ? "external spool holder"
    : `AMS ${target.amsNumber} · Slot ${target.trayNumber}`;
  const isPreconfiguration = !target.external && target.occupied === false;
  const title = target.spool
    ? target.external
      ? "Change external spool mapping"
      : "Change mapping"
    : "Assign spool";
  const description = isPreconfiguration
    ? `Choose the inventory record to preconfigure for ${location}.`
    : `Choose the inventory record for the physical spool in ${location}.`;
  const observed = target.external
    ? "External spool holder"
    : target.occupied === false
      ? "No spool currently detected"
      : target.printerName || "Filament details unavailable";

  function handleScan(results: IDetectedBarcode[]) {
    if (results.length !== 1) {
      setQrScanError(
        results.length === 0
          ? "No QR code detected. Please try again."
          : "Multiple QR codes detected. Keep only one code in view.",
      );
      return;
    }

    const spoolId = getSpoolIdFromQrValue(results[0].rawValue);
    if (!spoolId || !spools.some((spool) => spool.id === spoolId)) {
      setQrScanError("That QR code does not identify an available spool.");
      return;
    }

    if (scanHandledRef.current) return;
    scanHandledRef.current = true;

    setSelectedSpool(spoolId.toString());
    setQrScanning(false);
    setQrScanError(null);
    assignSpool(spoolId);
  }

  function assignSpool(spoolId: number) {
    setActionState({ error: null });
    setPendingAction("assign");
    startTransition(async () => {
      const result = await updateTrayAssignment(targetId, spoolId);
      setActionState(result);
      if (!result.error) {
        dialogRef.current?.close();
        onClose();
        router.refresh();
      } else {
        setPendingAction(null);
      }
    });
  }

  function clearAssignment() {
    setActionState({ error: null });
    setPendingAction("clear");
    startTransition(async () => {
      const result = await updateTrayAssignment(targetId, -1);
      setActionState(result);
      if (!result.error) {
        dialogRef.current?.close();
        onClose();
        router.refresh();
      } else {
        setPendingAction(null);
      }
    });
  }

  function saveAssignment() {
    assignSpool(Number(selectedSpool));
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="assignment-dialog-title"
      className="m-auto max-h-[calc(100dvh_-_1rem)] w-[calc(100%_-_1rem)] max-w-2xl overflow-hidden rounded-2xl border bg-card p-0 text-card-foreground shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-xs sm:w-[calc(100%_-_2rem)]"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <div className="flex max-h-[calc(100dvh_-_1rem)] flex-col">
        <div className="overflow-y-auto p-5 sm:p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2
                id="assignment-dialog-title"
                className="text-xl font-semibold tracking-tight"
              >
                {title}
              </h2>
              <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
                {description}
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-11 sm:size-9"
              aria-label="Close assignment dialog"
              onClick={onClose}
            >
              <X />
            </Button>
          </div>

          <div className="mt-5 flex items-center gap-3 rounded-xl border bg-muted/45 p-3.5">
            <span
              className="size-9 shrink-0 rounded-full border-[6px] border-card shadow-[0_0_0_1px_var(--border)]"
              style={{
                backgroundColor:
                  target.printerColorHex || "var(--muted-foreground)",
              }}
              aria-hidden="true"
            />
            <span>
              <span className="block text-[0.65rem] font-semibold uppercase tracking-widest text-muted-foreground">
                Printer reports
              </span>
              <span className="mt-0.5 block text-sm font-medium">
                {observed}
              </span>
            </span>
          </div>

          <p className="mt-4 text-xs leading-5 text-muted-foreground">
            Material and color are search hints only. Verify the physical spool
            before assigning it.
          </p>

          {qrScanError && (
            <Alert variant="destructive" className="mt-4">
              <AlertCircle />
              <AlertDescription>{qrScanError}</AlertDescription>
            </Alert>
          )}

          {qrScanning ? (
            <div className="mt-4">
              <Scanner
                onScan={handleScan}
                formats={["qr_code"]}
                components={{ torch: false }}
              />
              <Button
                type="button"
                variant="outline"
                className="mt-3 w-full"
                onClick={() => setQrScanning(false)}
              >
                Cancel scanning
              </Button>
            </div>
          ) : (
            <>
              <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                <label className="relative block">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <span className="sr-only">Search spools</span>
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    placeholder="Search spools"
                    className="h-11 w-full rounded-md border bg-background pl-9 pr-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:h-10"
                  />
                </label>
                {cameraAvailable && (
                  <Button
                    type="button"
                    variant="outline"
                    className="h-11 sm:h-9"
                    onClick={() => {
                      scanHandledRef.current = false;
                      setQrScanError(null);
                      setActionState({ error: null });
                      setQrScanning(true);
                    }}
                  >
                    <ScanLine />
                    Scan QR code
                  </Button>
                )}
              </div>

              <div
                className="mt-3 grid max-h-80 gap-2 overflow-y-auto overscroll-contain pr-1"
                role="radiogroup"
                aria-label="Spools"
              >
                {filteredSpools.length === 0 ? (
                  <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                    No spools match that search.
                  </p>
                ) : (
                  filteredSpools.map((spool) => {
                    const assignedLocation = assignments[spool.id];
                    const unavailable =
                      Boolean(assignedLocation) &&
                      spool.id.toString() !== currentSpoolId;
                    const checked = selectedSpool === spool.id.toString();

                    return (
                      <label
                        key={spool.id}
                        className={cn(
                          "grid grid-cols-[auto_auto_minmax(0,1fr)] items-center gap-3 rounded-xl border p-3 transition-colors sm:grid-cols-[auto_auto_minmax(0,1fr)_auto]",
                          unavailable
                            ? "cursor-not-allowed opacity-55"
                            : "cursor-pointer hover:border-primary/55 hover:bg-accent/50",
                          checked && "border-primary bg-accent",
                        )}
                      >
                        <input
                          type="radio"
                          name="dashboard-spool"
                          value={spool.id}
                          checked={checked}
                          disabled={unavailable || isPending}
                          onChange={(event) =>
                            setSelectedSpool(event.target.value)
                          }
                          className="size-4 accent-primary"
                        />
                        <span
                          className="size-7 rounded-full border-[5px] border-card shadow-[0_0_0_1px_var(--border)]"
                          style={{
                            backgroundColor:
                              spool.colorHex || "var(--muted-foreground)",
                          }}
                          aria-hidden="true"
                        />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-semibold">
                            #{spool.id} · {spool.name}
                          </span>
                          <span className="mt-0.5 block text-xs text-muted-foreground">
                            {[spool.vendor, spool.material]
                              .filter(Boolean)
                              .join(" · ") || "Filament details unavailable"}
                            {assignedLocation && (
                              <span className="font-medium text-amber-700 dark:text-amber-300">
                                {" · "}Assigned to {assignedLocation}
                              </span>
                            )}
                          </span>
                        </span>
                        <span className="col-start-3 text-left text-xs text-muted-foreground sm:col-start-auto sm:text-right">
                          <strong className="text-sm text-foreground sm:block">
                            {spool.remainingWeight.toFixed(0)} g
                          </strong>
                          <span className="ml-1 sm:ml-0">remaining</span>
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </>
          )}
        </div>

        {!qrScanning && (
          <div className="flex shrink-0 justify-end gap-2 border-t bg-muted/35 px-5 py-4 sm:px-6">
            {target.spool && (
              <Button
                type="button"
                variant="outline"
                className="mr-auto h-11 sm:h-9"
                disabled={isPending}
                onClick={clearAssignment}
              >
                <ButtonLoading
                  loading={pendingAction === "clear" && isPending}
                />
                Clear assignment
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              className="h-11 sm:h-9"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="button"
              className="h-11 sm:h-9"
              onClick={saveAssignment}
              disabled={
                isPending || !selectedSpool || selectedSpool === currentSpoolId
              }
            >
              <ButtonLoading
                loading={pendingAction === "assign" && isPending}
              />
              {target.spool ? "Save mapping" : "Assign selected spool"}
            </Button>
          </div>
        )}

        {actionState.error && (
          <div className="border-t px-5 py-3 sm:px-6">
            <Alert variant="destructive">
              <AlertCircle />
              <AlertDescription>{actionState.error}</AlertDescription>
            </Alert>
          </div>
        )}
      </div>
    </dialog>
  );
}
