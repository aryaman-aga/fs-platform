import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useAuth } from "@/auth/AuthContext";

import { Button } from "./ui";

/** Shown where a signed-in person nobody has given a role yet (e.g. they created their own account) would see an empty
 * list: why nothing shows and what to do. Place it inside an empty-state box. */
export function NoRoleYet() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try {
      await refresh(); // a role given meanwhile shows at once
      await qc.invalidateQueries(); // and so do the clients and engagements it opens
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack" style={{ alignItems: "center" }} data-testid="no-role">
      <p style={{ margin: 0 }}>You're signed in, but you don't have access to any clients yet. Ask your FS-Platform administrator to give you a role.</p>
      {me ? (
        <p className="small" style={{ margin: 0 }}>
          Tell them the e-mail address you signed in with: <strong>{me.user.email}</strong>
        </p>
      ) : null}
      <Button busy={busy} onClick={() => void check()}>
        Check again
      </Button>
    </div>
  );
}
