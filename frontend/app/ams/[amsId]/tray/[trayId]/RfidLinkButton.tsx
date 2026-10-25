"use client";

import { Button } from "@/components/ui/button";
import { useState } from "react";
import { linkRfidMapping } from "@/lib/client/rfid";
import { Alert } from "@/components/ui/alert";
import { useRouter } from "next/navigation";

type Props = {
  spoolId: number;
  uuid: string;
};

export function RfidLinkButton({ spoolId, uuid }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const onClick = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await linkRfidMapping(spoolId, uuid);
      setError(result.error);
      if (!result.error) router.refresh();
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {error && <Alert variant="destructive">{error}</Alert>}
      <Button
        variant="default"
        className="mt-4"
        onClick={onClick}
        disabled={loading}
      >
        Link Selected Spool to RFID Tag
      </Button>
    </>
  );
}
