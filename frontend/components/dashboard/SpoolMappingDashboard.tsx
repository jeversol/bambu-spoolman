"use client";

import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  LockKeyhole,
  WifiOff,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type DashboardSpool,
  type DashboardTray,
  describeColorHex,
} from "@/lib/dashboard";
import { cn } from "@/lib/utils";
import { RfidMappingDialog } from "./RfidMappingDialog";
import {
  type AssignmentTarget,
  SpoolAssignmentDialog,
} from "./SpoolAssignmentDialog";

type Props = {
  connected: boolean;
  rfidEnabled: boolean;
  trays: DashboardTray[];
  externalSpool: DashboardSpool | null;
  spools: DashboardSpool[];
  assignments: Record<number, string>;
};

const DASHBOARD_REFRESH_INTERVAL_MS = 5_000;

function useDashboardAutoRefresh(paused: boolean) {
  const router = useRouter();

  useEffect(() => {
    if (paused) return;

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        router.refresh();
      }
    };

    const interval = window.setInterval(
      refreshWhenVisible,
      DASHBOARD_REFRESH_INTERVAL_MS,
    );
    document.addEventListener("visibilitychange", refreshWhenVisible);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [paused, router]);
}

function SpoolMeter({ spool }: { spool: DashboardSpool | null }) {
  const percentage = spool?.remainingPercent;
  const level = percentage == null ? 0 : Math.round(percentage);
  const remainingWeight =
    spool && Number.isFinite(spool.remainingWeight)
      ? Math.max(0, Math.round(spool.remainingWeight))
      : null;

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
        remainingWeight == null
          ? "Remaining weight unknown"
          : percentage == null
            ? `${remainingWeight} grams remaining`
            : `${remainingWeight} grams remaining, ${level} percent of the spool`
      }
      role="img"
    >
      <span className="absolute inset-[15px] rounded-full border-4 border-card bg-muted shadow-[0_0_0_1px_var(--border)]" />
      <span className="relative whitespace-nowrap text-[0.8125rem] font-bold tracking-tight tabular-nums">
        {remainingWeight == null ? "?" : `${remainingWeight} g`}
      </span>
    </div>
  );
}

function TrayCard({
  tray,
  rfidEnabled,
  onAssign,
  onRfidDetails,
}: {
  tray: DashboardTray;
  rfidEnabled: boolean;
  onAssign: (tray: DashboardTray) => void;
  onRfidDetails: (tray: DashboardTray) => void;
}) {
  const needsMapping = tray.occupied === true && !tray.spool;
  const isEmpty = tray.occupied === false;
  const displayName = tray.printerName || tray.spool?.name;
  const filamentColor = tray.printerColorHex || tray.spool?.colorHex;
  const canManageRfid = Boolean(tray.rfidTag && tray.spool);
  const printerColorName = describeColorHex(tray.printerColorHex);
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
          Slot {tray.trayNumber}
        </span>
        {tray.locked ? (
          <Badge className="border-0 bg-primary/12 text-primary hover:bg-primary/12">
            <LockKeyhole /> RFID linked
          </Badge>
        ) : tray.rfidTag ? (
          <Badge className="border-0 bg-sky-500/15 text-sky-800 hover:bg-sky-500/15 dark:text-sky-300">
            RFID detected
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
        <p className="flex items-center gap-2.5 text-[0.95rem] font-semibold leading-5">
          {filamentColor && (
            <span
              className="h-6 w-9 shrink-0 rounded-md border border-black/20 shadow-[inset_0_0_0_1px_rgb(255_255_255_/_0.18),0_0_0_1px_var(--border)]"
              style={{ backgroundColor: filamentColor }}
              role="img"
              aria-label={`Filament color ${filamentColor}`}
              title={`Filament color ${filamentColor}`}
            />
          )}
          {tray.printerName
            ? `Bambu Lab ${tray.printerName}`
            : displayName ||
              (isEmpty ? "No spool detected" : "Filament unknown")}
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {tray.rfidTag && !tray.locked
            ? `${printerColorName ? `${printerColorName} · ` : ""}${
                rfidEnabled
                  ? "RFID detected, not linked to Spoolman"
                  : "RFID detected · SPOOLMAN_RFID_FIELD_KEY not configured"
              }`
            : tray.printerName
              ? `${printerColorName ? `${printerColorName} · ` : ""}Reported by printer`
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
                ? `#${tray.spool.id} · ${tray.spool.name}`
                : "No spool assigned"}
            </span>
          </span>
        </div>
      </div>

      {tray.locked || (rfidEnabled && canManageRfid) ? (
        <Button
          variant="outline"
          className="mx-4 mb-4 mt-4 h-11 sm:h-9"
          disabled={!canManageRfid}
          onClick={() => onRfidDetails(tray)}
        >
          {tray.locked ? "View RFID mapping" : "Link spool to RFID"}
        </Button>
      ) : (
        <Button
          variant={needsMapping ? "default" : "outline"}
          className="mx-4 mb-4 mt-4 h-11 sm:h-9"
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
  rfidEnabled,
  trays,
  externalSpool,
  spools,
  assignments,
}: Props) {
  const [assignmentTarget, setAssignmentTarget] =
    useState<AssignmentTarget | null>(null);
  const [rfidTarget, setRfidTarget] = useState<DashboardTray | null>(null);
  useDashboardAutoRefresh(assignmentTarget !== null || rfidTarget !== null);
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
            Slot occupancy and RFID information may be out of date.
          </div>
        </div>
      )}

      {needsMapping.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/35 bg-amber-500/10 p-4 text-sm text-amber-900 dark:text-amber-200">
          <AlertTriangle className="size-4 shrink-0" />
          <div className="min-w-[16rem] flex-1 leading-6">
            <strong>
              AMS {needsMapping[0].amsNumber} · Slot{" "}
              {needsMapping[0].trayNumber}
              {needsMapping.length === 1
                ? " needs a mapping."
                : ` and ${needsMapping.length - 1} other ${needsMapping.length === 2 ? "slot needs" : "slots need"} mappings.`}
            </strong>{" "}
            Usage from an unmapped slot cannot be tracked yet.
          </div>
          <Button
            type="button"
            variant="outline"
            className="border-amber-600/45 bg-transparent hover:bg-amber-500/10 dark:border-amber-300/35"
            onClick={() => setAssignmentTarget(needsMapping[0])}
          >
            Assign slot {needsMapping[0].trayNumber}
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
              className="h-11 sm:h-9"
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
                rfidEnabled={rfidEnabled}
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
