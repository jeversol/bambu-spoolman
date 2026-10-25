"use client";

import { Button } from "@/components/ui/button";
import { updateTrayAssignment } from "@/components/tray-config/actions";
import { Alert } from "@/components/ui/alert";
import { useState } from "react";

type Props = {
  trayId: number;
  locked: boolean;
};

export function ClearButton({ trayId, locked }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handleClear = async () => {
    setLoading(true);
    try {
      const result = await updateTrayAssignment(trayId, -1);
      setError(result.error);
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Could not remove spool",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {error && <Alert variant="destructive">{error}</Alert>}
      <Button
        variant="outline"
        disabled={locked || loading}
        className="ml-auto"
        onClick={handleClear}
      >
        Remove Spool
      </Button>
    </>
  );
}
