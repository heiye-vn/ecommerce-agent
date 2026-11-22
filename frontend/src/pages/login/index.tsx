import { Button, Form, Input } from "antd"
import { ArrowRightOutlined, InfoCircleOutlined } from "@ant-design/icons"
import { useState } from "react"

import { LoginError, type LoginValues } from "../../auth"
import styles from "./index.module.css"

type LoginPageProps = {
  onLogin: (values: LoginValues) => Promise<void>
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  async function handleSubmit(values: LoginValues) {
    if (submitting) return
    setSubmitting(true)
    setError("")
    try {
      await onLogin(values)
    } catch (failure) {
      setError(failure instanceof LoginError ? failure.message : "暂时无法登录，请稍后重试。")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">CA</span>
          <span className={styles.brandName}>CommerceAgent</span>
          <span className={styles.brandDivider} aria-hidden="true" />
          <span className={styles.brandDescription}>电商运营工作台</span>
        </div>
        <span className={styles.sandboxBadge}>本地业务沙箱</span>
      </header>

      <main className={styles.main}>
        <section className={styles.hero} aria-label="产品介绍">
          <div className={styles.heroContent}>
            <h1>从运营想法，<br />到可追溯的执行。</h1>
            <p className={styles.heroSummary}>一个工作台，串起查数、方案、人工审批与业务执行。</p>

            <div className={styles.featureList}>
              <div className={styles.featureRow}>
                <svg className={styles.featureIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <circle cx="10.5" cy="10.5" r="6.5" />
                  <path d="m15.5 15.5 4 4" />
                </svg>
                <span className={styles.featureTitle}>查询业务数据</span>
                <span>在当前店铺权限内获取事实</span>
              </div>
              <div className={styles.featureRow}>
                <svg className={styles.featureIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <rect x="6" y="4" width="12" height="16" rx="2" />
                  <path d="M9 4v3h6V4M9 10h6M9 14h4" />
                </svg>
                <span className={styles.featureTitle}>复核执行方案</span>
                <span>核对参数、数据来源和风险</span>
              </div>
              <div className={styles.featureRow}>
                <svg className={styles.featureIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <path d="M12 3 19 6v5c0 4.6-3.2 8-7 10-3.8-2-7-5.4-7-10V6l7-3Z" />
                  <path d="m9 12 2 2 4-4" />
                </svg>
                <span className={styles.featureTitle}>审批并追踪</span>
                <span>人工裁决，执行过程可追溯</span>
              </div>
            </div>
          </div>
        </section>

        <section className={styles.loginPanel} aria-label="登录表单">
          <div className={styles.formWrapper}>
            <h2>登录工作台</h2>
            <p className={styles.loginSummary}>使用组织分配的账号访问已授权店铺。</p>

            <Form<LoginValues>
              layout="vertical"
              initialValues={{ username: "ops.demo" }}
              requiredMark={false}
              className={styles.loginForm}
              disabled={submitting}
              onFinish={handleSubmit}
              onValuesChange={() => setError("")}
            >
              <Form.Item
                name="username"
                label="账号"
                rules={[{ required: true, whitespace: true, message: "请输入账号" }]}
              >
                <Input name="username" autoComplete="username" maxLength={120} />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[{ required: true, message: "请输入密码" }]}
              >
                <Input.Password name="password" autoComplete="current-password" maxLength={256} />
              </Form.Item>
              {error && <p className={styles.error} role="alert">{error}</p>}
              <Form.Item className={styles.submitItem}>
                <Button type="primary" htmlType="submit" icon={<ArrowRightOutlined />} loading={submitting} block>
                  登录工作台
                </Button>
              </Form.Item>
            </Form>

            <aside className={styles.sandboxNote}>
              <InfoCircleOutlined aria-hidden="true" />
              <div>
                <strong>沙箱环境</strong>
                <p>仅接入本地模拟数据，不连接真实店铺或支付渠道。</p>
              </div>
            </aside>
          </div>
        </section>
      </main>
    </div>
  )
}
