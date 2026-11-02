import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  BookOpen,
  ChartNoAxesCombined,
  ChevronRight,
  Headphones,
  History,
  LogOut,
  Menu,
  MessageSquare,
  Search,
  Ticket,
  UserRound,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { tokens } from "../api/client";
import { useAuth } from "../state/auth";
import { useRoute } from "../app/router";
import type { PageId } from "../app/router";
import { Brand, Button, Dialog, ErrorState } from "../components/ui";
import { useTask } from "../hooks/useTask";
type Link = { page: PageId; label: string; icon: LucideIcon };
export function AppLayout({ children }: { children: ReactNode }) {
  const user = useAuth((s) => s.user)!;
  const { page } = useRoute();
  const admin = user.role === "admin";
  const [menu, setMenu] = useState(false);
  const [logout, setLogout] = useState(false);
  const task = useTask();
  const cache = useQueryClient();
  useEffect(() => {
    setMenu(false);
  }, [page]);
  useEffect(() => {
    const viewport = window.visualViewport;
    const resize = () =>
      document.documentElement.style.setProperty(
        "--viewport-height",
        (viewport?.height || window.innerHeight) + "px",
      );
    resize();
    viewport?.addEventListener("resize", resize);
    window.addEventListener("resize", resize);
    return () => {
      viewport?.removeEventListener("resize", resize);
      window.removeEventListener("resize", resize);
      document.documentElement.style.removeProperty("--viewport-height");
    };
  }, []);
  const links: Link[] = admin
    ? [
        { page: "chat", label: "会话工作台", icon: MessageSquare },
        { page: "tickets", label: "工单管理", icon: Ticket },
        { page: "handoff", label: "人工协同", icon: Headphones },
        { page: "knowledge", label: "知识文档", icon: BookOpen },
        { page: "search", label: "检索测试", icon: Search },
        { page: "analytics", label: "运行分析", icon: ChartNoAxesCombined },
        { page: "users", label: "用户管理", icon: Users },
      ]
    : [
        { page: "chat", label: "我的智能客服", icon: MessageSquare },
        { page: "history", label: "会话历史", icon: History },
        { page: "tickets", label: "我的工单", icon: Ticket },
      ];
  const navigation = (
    <>
      <div className="workspace">
        {admin ? "客服运营工作区" : "个人服务空间"}
        <ChevronRight size={14} />
      </div>
      <nav aria-label="主导航">
        {links.map(({ page: id, label, icon: Icon }) => (
          <a
            key={id}
            href={"#/" + id}
            aria-current={page === id ? "page" : undefined}
          >
            <Icon size={18} strokeWidth={1.6} />
            {label}
          </a>
        ))}
      </nav>
      <div className="nav-bottom">
        <a
          href="#/account"
          aria-current={page === "account" ? "page" : undefined}
        >
          <UserRound size={18} />
          <span>
            {user.nickname || user.username}
            <small>{admin ? "管理员账户" : "个人账户"}</small>
          </span>
        </a>
        <Button
          tone="plain"
          onClick={() => {
            setMenu(false);
            setLogout(true);
          }}
        >
          <LogOut size={16} />
          退出登录
        </Button>
      </div>
    </>
  );
  return (
    <div className={"application " + (admin ? "operator" : "customer")}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="app-sidebar">
        <Brand />
        {navigation}
      </aside>
      <div className="app-main">
        <header className="frame-bar">
          <Button
            className="menu-button"
            tone="plain"
            aria-label="打开导航"
            onClick={() => setMenu(true)}
          >
            <Menu size={20} />
          </Button>
          <span>
            {admin ? "运营工作区" : "客户服务"} <ChevronRight size={13} />{" "}
            {links.find((x) => x.page === page)?.label || "账户"}
          </span>
          <span className="frame-account">
            {user.username} · {admin ? "管理员" : "普通用户"}
          </span>
        </header>
        <main className="work-sheet" id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
      {menu && (
        <Dialog title="AICSHub · 导航" onClose={() => setMenu(false)}>
          <div className="mobile-navigation">{navigation}</div>
        </Dialog>
      )}
      {logout && (
        <Dialog
          title="退出当前账户"
          busy={task.pending}
          onClose={() => setLogout(false)}
        >
          <p>退出将撤销当前登录，请确认正在处理的操作已经完成。</p>
          <ErrorState error={task.error} />
          <div className="form-actions">
            <Button onClick={() => setLogout(false)} disabled={task.pending}>
              取消
            </Button>
            <Button
              tone="primary"
              busy={task.pending}
              onClick={() =>
                task.run(async () => {
                  await api.logout(tokens.refresh);
                  useAuth.getState().clear();
                  cache.clear();
                  window.location.hash = "/chat";
                })
              }
            >
              确认退出
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
