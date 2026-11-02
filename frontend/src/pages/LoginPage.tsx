import { useState } from "react";
import { ArrowRight, BookOpen, Headphones, MessageSquare } from "lucide-react";
import { api } from "../api";
import { ApiError } from "../api/client";
import { useAuth } from "../state/auth";
import { useTask } from "../hooks/useTask";
import { Brand, Button, ErrorState, Field, Notice } from "../components/ui";
export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const task = useTask();
  const expired = useAuth((s) => s.expired);
  const bootError = useAuth((s) => s.error) as Error | null;
  const localDemo =
    import.meta.env.VITE_SHOW_DEMO_ACCOUNTS === "true" &&
    ["localhost", "127.0.0.1"].includes(window.location.hostname);
  return (
    <div className="login-page">
      <header>
        <Brand />
      </header>
      <main className="login-grid">
        <section className="login-intro">
          <h1>
            让每个问题，
            <br />
            都有清楚的回应。
          </h1>
          <p>
            从第一次咨询，到问题解决。
            <br />
            智能客服、知识依据与人工服务，连接在一起。
          </p>
          <ol className="service-path">
            <li>
              <MessageSquare size={20} />
              <div>
                <strong>说出你的问题</strong>
                <span>咨询政策、查询订单，继续之前的对话。</span>
              </div>
            </li>
            <li>
              <BookOpen size={20} />
              <div>
                <strong>了解回答的依据</strong>
                <span>查看知识来源，留下有帮助或需改进的反馈。</span>
              </div>
            </li>
            <li>
              <Headphones size={20} />
              <div>
                <strong>需要时，让人工接力</strong>
                <span>提交工单或转人工，保留问题的上下文。</span>
              </div>
            </li>
          </ol>
        </section>
        <section className="login-form">
          <h2>登录 AICSHub</h2>
          <p>使用你的账户进入智能客服。</p>
          {expired && (
            <Notice kind="warning">登录已过期，请重新登录后继续。</Notice>
          )}
          {bootError && <ErrorState error={bootError} />}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void task.run(async () => {
                const result = await api.login(username.trim(), password);
                useAuth.getState().accept(result);
              });
            }}
          >
            <Field label="用户名">
              <input
                name="username"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="输入用户名"
                disabled={task.pending}
              />
            </Field>
            <div className="field">
              <label htmlFor="login-password">密码</label>
              <div className="password-input">
                <input
                  id="login-password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="输入密码"
                  disabled={task.pending}
                />
                <Button
                  type="button"
                  tone="plain"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  onClick={() => setShowPassword((v) => !v)}
                >
                  {showPassword ? "隐藏" : "显示"}
                </Button>
              </div>
            </div>
            <ErrorState
              error={
                task.error instanceof ApiError && task.error.status === 401
                  ? new Error("用户名或密码错误，请检查后重试。")
                  : task.error
              }
            />
            <Button
              type="submit"
              tone="primary"
              busy={task.pending}
              className="login-submit"
            >
              {task.pending ? "正在登录" : "登录"}
              <ArrowRight size={17} />
            </Button>
          </form>
          <p className="login-note">
            账户角色由服务端验证，登录后进入对应工作区。
          </p>
          {localDemo && (
            <Notice>
              本机演示账户需由后端启用。用户：user001 / user12345；管理员：admin
              / admin12345。
            </Notice>
          )}
        </section>
      </main>
      <footer>智能服务，有据可循。</footer>
    </div>
  );
}
