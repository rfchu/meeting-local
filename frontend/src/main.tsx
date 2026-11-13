import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/tokens.css";
import "./style.css";
import "./minutes.css";
import "./features/notes/recordingWorkbench.css";

const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

async function mount() {
  const browserWorkspace = await fetch("/api/runtime/workspace-mode")
    .then(
      async (response) =>
        response.ok && (await response.json()).mode === "browser",
    )
    .catch(() => false);
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <App browserWorkspace={browserWorkspace} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

void mount();
