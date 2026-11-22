import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  const { API_PROXY_TARGET } = loadEnv(mode, ".", "API_")
  const proxy = { "/api": { target: API_PROXY_TARGET ?? "http://127.0.0.1:7620" } }
  return { plugins: [react()], server: { proxy }, preview: { proxy } }
})
