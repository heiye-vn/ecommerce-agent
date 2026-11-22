import React from "react"
import ReactDOM from "react-dom/client"
import { App as AntApp, ConfigProvider } from "antd"
import zhCN from "antd/locale/zh_CN"

import AppRouter from "./router"
import "./global.css"
import { designTokens } from "./theme/designTokens"

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={designTokens}>
      <AntApp>
        <AppRouter />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
)
