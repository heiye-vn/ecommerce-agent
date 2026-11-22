import type { ThemeConfig } from "antd"

export const designTokens: ThemeConfig = {
  token: {
    colorPrimary: "#000000",
    colorBgLayout: "#FBFBF5",
    colorBgContainer: "#FFFFFF",
    colorBorder: "#E4E4E7",
    colorText: "#000000",
    colorTextSecondary: "#52525B",
    borderRadius: 8,
    fontFamily: "Inter Variable, Inter, Helvetica, Arial, sans-serif",
    fontSize: 16,
  },
  components: {
    Button: {
      borderRadius: 9999,
      controlHeight: 40,
      paddingContentHorizontal: 24,
    },
  },
}
