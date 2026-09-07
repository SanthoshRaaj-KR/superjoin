import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { StoreProvider } from "./store";
import "./theme.css";

const el = document.getElementById("root");
if (!el) throw new Error("#root missing from index.html");

createRoot(el).render(
  <StrictMode>
    <StoreProvider>
      <App />
    </StoreProvider>
  </StrictMode>,
);
