import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "@/test/render";

import { useRunProgress } from "./runs";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((ev: MessageEvent<string>) => void)[]> = {};
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (ev: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  emit(type: string, data: unknown) {
    for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
  close() {
    this.closed = true;
  }
}

const run = (status: string) => ({ id: "r1", kind: "check", status, error_code: null, error_message: null });

describe("useRunProgress", () => {
  let status = "running";
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    status = "running";
    mockApi({ "/api/v1/runs/r1": () => run(status) });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reports a run that already finished without opening a stream", async () => {
    status = "succeeded";
    const onDone = vi.fn();
    const { result } = renderHook(() => useRunProgress("r1", onDone));
    await waitFor(() => expect(result.current.done).toBe(true));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("streams progress, then reports the final record once even if the stream also errors", async () => {
    const onDone = vi.fn();
    const { result } = renderHook(() => useRunProgress("r1", onDone));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const es = FakeEventSource.instances[0]!;
    expect(es.url).toBe("/api/v1/runs/r1/events");
    act(() => es.emit("progress", { id: 1, kind: "progress", message: "casting totals", pct: 0.4, ts: "" }));
    expect(result.current.pct).toBe(0.4);
    expect(result.current.message).toBe("casting totals");
    status = "succeeded";
    act(() => {
      es.emit("end", { status: "succeeded" });
      es.onerror?.();
    });
    await waitFor(() => expect(result.current.done).toBe(true));
    expect(es.closed).toBe(true);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0]?.[0]).toMatchObject({ id: "r1", status: "succeeded" });
  });
});
