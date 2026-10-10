import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// On your laptop, requests to /api go to the FastAPI backend on port 8000.
// On AWS, VITE_API_URL points the site at the Lambda function URL instead.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
