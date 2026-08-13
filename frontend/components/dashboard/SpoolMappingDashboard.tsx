"use client";

import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  LockKeyhole,
  WifiOff,
} from "lucide-react";
import { useState } from "react";
import { RfidMappingDialog } from "./RfidMappingDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { DashboardSpool, DashboardTray } from "@/lib/dashboard";
import { cn } from "@/lib/utils";
import {
  type AssignmentTarget,
  SpoolAssignmentDialog,
} from "./SpoolAssignmentDialog";

type Props = {
  connected: boolean;
  trays: DashboardTray[];
  externalSpool: DashboardSpool | null;
  spools: DashboardSpool[];
  assignments: Record<number, string>;
};

function SpoolMeter({ spool }: { spool: DashboardSpool | null }) {
  const percentage = spool?.remainingPercent;
  const level = percentage == null ? 0 : Math.round(percentage);

  return (
    <div
      className="relative mx-auto my-2 grid size-24 place-items-center rounded-full border-[8px] border-muted shadow-[inset_0_0_0_1px_var(--border)]"
      style={{
        background:
          percentage == null
            ? "var(--muted)"
            : `conic-gradient(var(--primary) ${level}%, var(--muted) 0)`,
      }}
      aria-label={
        percentage == null
          ? "Remaining amount unknown"
          : `${level} percent remaining`
      }
      role="img"
    >
      <span className="absolute inset-[17px] rounded-full border-[5px] border-card bg-muted shadow-[0_0_0_1px_var(--border)]" />
      <span className="relative text-xs font-bold">
        {percentage == null ? "?" : `${level}%`}
      </span>
    </div>
  );
}

function TrayCard({
  tray,
  onAssign,
  onRfidDetails,
}: {
  tray: DashboardTray;
  onAssign: (tray: DashboardTray) => void;
  onRfidDetails: (tray: DashboardTray) => void;
}) {
  const needsMapping = tray.occupied === true && !tray.spool;
  const isEmpty = tray.occupied === false;
  const displayName = tray.printerName || tray.spool?.name;
  const canManageRfid = Boolean(tray.rfidTag && tray.spool);
  return (
    <article
      className={cn(
        "flex min-h-80 flex-col overflow-hidden rounded-2xl border bg-card shadow-sm transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-foreground/20 hover:shadow-lg",
        needsMapping && "border-amber-500/55",
        isEmpty && "max-sm:min-h-0",
      )}
    >
      <div className="flex items-center justify-between gap-2 px-4 pb-2 pt-4">
        <span className="text-xs font-bold uppercase tracking-widest">
          Tray {tray.trayNumber}
        </span>
        {tray.locked ? (
          <Badge className="border-0 bg-primary/12 text-primary hover:bg-primary/12">
            <LockKeyhole /> RFID linked
          </Badge>
        ) : needsMapping ? (
          <Badge className="border-0 bg-amber-500/15 text-amber-800 hover:bg-amber-500/15 dark:text-amber-300">
            Needs mapping
          </Badge>
        ) : isEmpty ? (
          <Badge variant="secondary">Empty</Badge>
        ) : tray.spool ? (
          <Badge variant="secondary">Manual</Badge>
        ) : (
          <Badge variant="secondary">Unassigned</Badge>
        )}
      </div>

      <div className={cn(isEmpty && "max-sm:hidden")}>
        <SpoolMeter spool={tray.spool} />
      </div>

      <div className={cn("flex-1 px-4", isEmpty && "max-sm:py-3")}>
        <p className="flex items-center gap-2 text-[0.95rem] font-semibold leading-5">
          {(tray.printerColorHex || tray.spool?.colorHex) && (
            <span
              className="size-3 shrink-0 rounded-full border-2 border-card shadow-[0_0_0_1px_var(--border)]"
              style={{
                backgroundColor:
                  tray.printerColorHex || tray.spool?.colorHex || undefined,
              }}
              aria-hidden="true"
            />
          )}
          {displayName || (isEmpty ? "No spool detected" : "Filament unknown")}
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {tray.printerName
            ? "Reported by printer"
            : isEmpty
              ? "Assign a spool now or wait until one is inserted."
              : "Printer details unavailable"}
        </p>

        <div
          className={cn(
            "mt-3 flex gap-2 border-t pt-3",
            isEmpty && "max-sm:hidden",
          )}
        >
          <ArrowRight className="mt-0.5 size-4 shrink-0 text-primary" />
          <span className="min-w-0">
            <span className="block text-[0.65rem] font-semibold uppercase tracking-widest text-muted-foreground">
              {tray.spool ? "Mapped to spool" : "Spool mapping"}
            </span>
            <span className="mt-0.5 block truncate text-xs font-semibold">
              {tray.spool
                ? `#${tray.spool.id} · ${tray.spool.name} · ${tray.spool.remainingWeight.toFixed(0)} g`
                : "No spool assigned"}
            </span>
          </span>
        </div>
      </div>

      {tray.locked ? (
        <Button
          variant="outline"
          className="mx-4 mb-4 mt-4"
          disabled={!canManageRfid}
          onClick={() => onRfidDetails(tray)}
        >
          {canManageRfid ? "View RFID mapping" : "RFID details unavailable"}
        </Button>
      ) : (
        <Button
          variant={needsMapping ? "default" : "outline"}
          className="mx-4 mb-4 mt-4"
          onClick={() => onAssign(tray)}
        >
          {tray.spool ? "Change mapping" : "Assign spool"}
        </Button>
      )}
    </article>
  );
}

export function SpoolMappingDashboard({
  connected,
  trays,
  externalSpool,
  spools,
  assignments,
}: Props) {
  const [assignmentTarget, setAssignmentTarget] =
    useState<AssignmentTarget | null>(null);
  const [rfidTarget, setRfidTarget] = useState<DashboardTray | null>(null);
  const needsMapping = trays.filter(
    (tray) => tray.occupied === true && !tray.spool,
  );
  const groupedTrays = trays.reduce<Map<number, DashboardTray[]>>(
    (groups, tray) => {
      const group = groups.get(tray.amsNumber) ?? [];
      group.push(tray);
      groups.set(tray.amsNumber, group);
      return groups;
    },
    new Map(),
  );

  const externalTarget: AssignmentTarget = {
    id: 255,
    amsNumber: 0,
    trayNumber: 0,
    occupied: null,
    printerName: null,
    printerColorHex: null,
    spool: externalSpool,
    external: true,
  };

  return (
    <>
      <h1 className="sr-only">Printer mappings</h1>

      {!connected && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-destructive/35 bg-destructive/10 p-4 text-sm text-destructive">
          <WifiOff className="mt-0.5 size-4 shrink-0" />
          <div>
            <strong className="font-semibold">Printer disconnected.</strong>{" "}
            Tray occupancy and RFID information may be out of date.
          </div>
        </div>
      )}

      {needsMapping.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/35 bg-amber-500/10 p-4 text-sm text-amber-900 dark:text-amber-200">
          <AlertTriangle className="size-4 shrink-0" />
          <div className="min-w-[16rem] flex-1 leading-6">
            <strong>
              AMS {needsMapping[0].amsNumber} · Tray{" "}
              {needsMapping[0].trayNumber}
              {needsMapping.length === 1
                ? " needs a mapping."
                : ` and ${needsMapping.length - 1} other ${needsMapping.length === 2 ? "tray need" : "trays need"} mappings.`}
            </strong>{" "}
            Usage from an unmapped tray cannot be tracked yet.
          </div>
          <Button
            type="button"
            variant="outline"
            className="border-amber-600/45 bg-transparent hover:bg-amber-500/10 dark:border-amber-300/35"
            onClick={() => setAssignmentTarget(needsMapping[0])}
          >
            Assign tray {needsMapping[0].trayNumber}
          </Button>
        </div>
      )}

      <section aria-labelledby="external-spool-title">
        <h2 id="external-spool-title" className="sr-only">
          External spool
        </h2>
        <details className="group overflow-hidden rounded-xl border bg-card">
          <summary className="flex min-h-12 cursor-pointer list-none items-center gap-2.5 px-3.5 py-2.5 hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
            <span className="text-sm font-semibold">External spool</span>
            <Badge variant="secondary">
              {externalSpool ? "Configured" : "Not configured"}
            </Badge>
            <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">
              {externalSpool ? "View configured mapping" : "Assign a spool"}
            </span>
            <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
          </summary>
          <div className="grid items-center gap-4 border-t bg-muted/20 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto_auto]">
            <span
              className="size-10 rounded-full border-[7px] border-muted shadow-[0_0_0_1px_var(--border)]"
              style={{
                backgroundColor:
                  externalSpool?.colorHex || "var(--muted-foreground)",
              }}
              aria-hidden="true"
            />
            <div>
              <p className="text-sm font-semibold">
                {externalSpool
                  ? `Spool #${externalSpool.id} · ${externalSpool.name}`
                  : "No spool assigned"}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {externalSpool
                  ? "Configured mapping"
                  : "Assign the spool currently on the external holder."}
              </p>
            </div>
            {externalSpool && (
              <div className="sm:text-right">
                <strong className="block text-lg">
                  {externalSpool.remainingWeight.toFixed(0)} g
                </strong>
                <span className="text-xs text-muted-foreground">
                  {externalSpool.remainingPercent == null
                    ? "Remaining percentage unknown"
                    : `${Math.round(externalSpool.remainingPercent)}% remaining`}
                </span>
              </div>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={() => setAssignmentTarget(externalTarget)}
            >
              {externalSpool ? "Change mapping" : "Assign spool"}
            </Button>
          </div>
        </details>
      </section>

      {[...groupedTrays.entries()].map(([amsNumber, amsTrays]) => (
        <section
          key={amsNumber}
          className="mt-7"
          aria-labelledby={`ams-${amsNumber}`}
        >
          <h2
            id={`ams-${amsNumber}`}
            className="mb-3 text-lg font-semibold tracking-tight"
          >
            AMS {amsNumber}
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {amsTrays.map((tray) => (
              <TrayCard
                key={tray.id}
                tray={tray}
                onAssign={setAssignmentTarget}
                onRfidDetails={setRfidTarget}
              />
            ))}
          </div>
        </section>
      ))}

      {trays.length === 0 && (
        <div className="mt-7 rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          No AMS is currently detected. You can still configure the external
          spool above.
        </div>
      )}

      {assignmentTarget && (
        <SpoolAssignmentDialog
          key={`${assignmentTarget.id}-${assignmentTarget.spool?.id ?? "none"}`}
          target={assignmentTarget}
          spools={spools}
          assignments={assignments}
          onClose={() => setAssignmentTarget(null)}
        />
      )}

      {rfidTarget && (
        <RfidMappingDialog
          key={`${rfidTarget.id}-${rfidTarget.rfidTag}`}
          tray={rfidTarget}
          onClose={() => setRfidTarget(null)}
        />
      )}
    </>
  );
}
