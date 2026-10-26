import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/api";

export function AccountingNotice() {
  const query = useQuery({
    queryKey: ["accounting-status"],
    queryFn: ({ signal }) =>
      apiRequest<{
        blocked: boolean;
        pending: { spoolId: number; lengthMm: number } | null;
      }>("/accounting", { signal }),
    refetchInterval: 5000,
  });
  if (query.data?.blocked) {
    return (
      <div role="alert" className="mx-auto max-w-6xl p-4 text-destructive">
        Filament accounting is paused. An update to spool{" "}
        {query.data.pending?.spoolId} needs confirmation; usage totals may be
        incomplete. Stop the container and follow the{" "}
        <a
          className="underline"
          href="https://github.com/jeversol/bambu-spoolman/blob/main/docs/consumption-recovery.md"
          target="_blank"
          rel="noreferrer"
        >
          consumption recovery instructions
        </a>
        .
      </div>
    );
  }
  return query.error ? (
    <div role="alert" className="mx-auto max-w-6xl p-4 text-destructive">
      Accounting status is unavailable. Check the service before relying on usage
      totals.
    </div>
  ) : null;
}
