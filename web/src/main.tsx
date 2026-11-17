import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./styles/workbench.css";
import "./i18n";
import { App } from "./App";
import { readSession } from "./api/client";

const session = readSession();
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App session={session} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
