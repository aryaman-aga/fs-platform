import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/** Query defaults: no retries on 4xx (permission / validation answers are final). */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      },
      mutations: { retry: false },
    },
  });
}
