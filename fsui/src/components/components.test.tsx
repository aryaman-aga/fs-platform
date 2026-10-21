import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DataGrid } from "./DataGrid";
import { ErrorBoundary } from "./ErrorBoundary";
import { Tabs } from "./Tabs";
import { ErrorNotice, SeverityBadge } from "./ui";
import { ApiError } from "@/api/client";
import { stepUpValid } from "./StepUp";

describe("DataGrid", () => {
  const rows = [
    { id: "a", n: "Alpha", v: 3 },
    { id: "b", n: "Beta", v: 1 },
    { id: "c", n: "Gamma", v: 2 },
  ];
  const columns = [
    { key: "n", header: "Name", render: (r: (typeof rows)[number]) => r.n, sortValue: (r: (typeof rows)[number]) => r.n },
    { key: "v", header: "Value", render: (r: (typeof rows)[number]) => r.v, sortValue: (r: (typeof rows)[number]) => r.v },
  ];

  it("moves with the arrow keys and opens with Enter", () => {
    const open = vi.fn();
    render(<DataGrid label="Things" rows={rows} rowKey={(r) => r.id} columns={columns} onOpen={open} />);
    const body = screen.getAllByRole("row").slice(1);
    body[0]?.focus();
    fireEvent.keyDown(body[0] as HTMLElement, { key: "ArrowDown" });
    expect(document.activeElement).toBe(body[1]);
    fireEvent.keyDown(body[1] as HTMLElement, { key: "End" });
    expect(document.activeElement).toBe(body[2]);
    fireEvent.keyDown(body[2] as HTMLElement, { key: "Enter" });
    expect(open).toHaveBeenCalledWith(rows[2]);
  });

  it("sorts when a sortable header is clicked", () => {
    render(<DataGrid label="Things" rows={rows} rowKey={(r) => r.id} columns={columns} />);
    fireEvent.click(screen.getByRole("columnheader", { name: /Value/ }));
    const cells = screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(cells).toEqual(["Beta1", "Gamma2", "Alpha3"]);
    expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveAttribute("aria-sort", "ascending");
  });
});

describe("Tabs", () => {
  function Harness() {
    const [t, setT] = useState("a");
    return <Tabs label="Sections" active={t} onChange={setT} tabs={[{ key: "a", label: "First" }, { key: "b", label: "Second" }, { key: "c", label: "Hidden", hidden: true }]} />;
  }
  it("switches with the arrow keys and skips hidden tabs", () => {
    render(<Harness />);
    const first = screen.getByRole("tab", { name: "First" });
    expect(screen.queryByRole("tab", { name: "Hidden" })).toBeNull();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Second" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("accessibility of status", () => {
  it("severity is a word and an icon, not only a colour", () => {
    render(<SeverityBadge severity="High" />);
    expect(screen.getByText("High")).toBeInTheDocument();
  });

  it("errors show the code and the correlation id, never a stack", () => {
    render(<ErrorNotice error={new ApiError(409, "gate_failed", "preconditions not met", "abc-123")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("preconditions not met");
    expect(screen.getByRole("alert")).toHaveTextContent("code gate_failed · ref abc-123");
  });
});

describe("step-up window", () => {
  it("needs a fresh code when the window has lapsed", () => {
    const now = new Date("2026-10-21T10:00:00Z");
    expect(stepUpValid("2026-10-21T10:04:00Z", now)).toBe(true);
    expect(stepUpValid("2026-10-21T10:00:05Z", now)).toBe(false);
    expect(stepUpValid(null, now)).toBe(false);
  });
});

describe("ErrorBoundary", () => {
  function Broken(): never {
    throw new Error("cannot read the run summary");
  }

  it("contains a screen that throws and shows only the message", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined); // React reports the caught error
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    quiet.mockRestore();
    expect(screen.getByRole("alert")).toHaveTextContent("This screen hit an error and was stopped.");
    expect(screen.getByRole("alert")).toHaveTextContent("cannot read the run summary");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
