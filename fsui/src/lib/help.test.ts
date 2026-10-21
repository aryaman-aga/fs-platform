import { describe, expect, it } from "vitest";

import { helpFor } from "./help";

describe("contextual help", () => {
  it("mentions the authenticator only where the installation requires one", () => {
    const text = (path: string, mfa?: boolean) => JSON.stringify(helpFor(path, mfa));
    expect(text("/admin/users")).toMatch(/MFA code/);
    expect(text("/admin/users", false)).not.toMatch(/MFA|authenticator|code/i);
    expect(text("/admin/users", false)).toMatch(/No role yet/);
    expect(text("/login")).toMatch(/authenticator app/);
    expect(text("/login", false)).not.toMatch(/MFA|authenticator|code/i);
    expect(helpFor("/clients", false)).toEqual(helpFor("/clients"));
  });
});
