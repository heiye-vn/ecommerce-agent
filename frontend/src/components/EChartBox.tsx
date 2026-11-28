import type * as echarts from "echarts"
import { useEffect, useRef } from "react"

type EChartBoxProps = {
  option: echarts.EChartsCoreOption
  height?: number
  ariaLabel: string
  className?: string
}

let echartsLoader: Promise<typeof import("echarts")> | null = null

function loadEcharts() {
  echartsLoader ??= import("echarts")
  return echartsLoader
}

export default function EChartBox({ option, height = 260, ariaLabel, className }: EChartBoxProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // jsdom 无真实渲染宽高时跳过按需加载与 init，避免解析重型包体及零尺寸警告
    if (el.clientWidth === 0 && el.clientHeight === 0) {
      el.setAttribute("data-chart-mode", "headless")
      return
    }
    el.setAttribute("data-chart-mode", "echarts")

    let cancelled = false
    let observer: ResizeObserver | null = null
    let chart: echarts.ECharts | null = null

    void loadEcharts().then((mod) => {
      if (cancelled || !containerRef.current) return
      const instance = mod.init(containerRef.current, null, { renderer: "svg" })
      chart = instance
      instance.setOption(option, true)
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(() => {
          instance.resize()
        })
        observer.observe(containerRef.current)
      }
    })

    return () => {
      cancelled = true
      observer?.disconnect()
      chart?.dispose()
    }
  }, [option])

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={ariaLabel}
      className={className}
      style={{ width: "100%", height }}
    />
  )
}
