import { Component, Suspense, lazy, useEffect } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../state/auth";
import { useChat } from "../state/chat";
import { adminPages, useRoute } from "./router";
import { AppLayout } from "../layouts/AppLayout";
import { Button, Empty, ErrorState, Loading } from "../components/ui";
import LoginPage from "../pages/LoginPage";
const ChatPage = lazy(() => import("../pages/ChatPage"));
const HistoryPage = lazy(() => import("../pages/HistoryPage"));
const TicketsPage = lazy(() => import("../pages/TicketsPage"));
const HandoffPage = lazy(() => import("../pages/HandoffPage"));
const KnowledgePage = lazy(() => import("../pages/KnowledgePage"));
const SearchPage = lazy(() => import("../pages/SearchPage"));
const AnalyticsPage = lazy(() => import("../pages/AnalyticsPage"));
const UsersPage = lazy(() => import("../pages/UsersPage"));
const AccountPage = lazy(() => import("../pages/AccountPage"));
class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Do not log user content or credentials. */
  }
  render() {
    return this.state.failed ? (
      <Empty
        title="页面暂时无法显示"
        description="已保存的数据仍在服务端，请重新加载页面。"
        action={
          <Button onClick={() => window.location.reload()}>重新加载</Button>
        }
      />
    ) : (
      this.props.children
    );
  }
}
export default function App() {
  const auth = useAuth();
  const route = useRoute();
  const queryClient = useQueryClient();
  useEffect(() => {
    void useAuth.getState().bootstrap();
    const expired = () => {
      useAuth.getState().clear(true);
      queryClient.clear();
    };
    const storage = (event: StorageEvent) => {
      if (event.key === "cs_access_token" && !event.newValue) expired();
      else if (event.key === "cs_user") {
        queryClient.clear();
        useChat.getState().clear();
        useAuth.setState({ user: null, ready: false });
        void useAuth.getState().bootstrap();
      }
    };
    window.addEventListener("auth-expired", expired);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener("auth-expired", expired);
      window.removeEventListener("storage", storage);
    };
  }, [queryClient]);
  if (!auth.ready)
    return (
      <div className="boot">
        <Loading label="正在确认登录状态" />
      </div>
    );
  if (!auth.user) return <LoginPage />;
  const forbidden =
    auth.user.role !== "admin" && adminPages.includes(route.page);
  const pages: Record<string, ReactNode> = {
    chat: <ChatPage />,
    history: <HistoryPage />,
    tickets: <TicketsPage />,
    handoff: <HandoffPage />,
    knowledge: <KnowledgePage />,
    search: <SearchPage />,
    analytics: <AnalyticsPage />,
    users: <UsersPage />,
    account: <AccountPage />,
  };
  return (
    <AppLayout>
      <ErrorBoundary key={route.page}>
        <Suspense fallback={<Loading />}>
          {forbidden ? (
            <>
              <h1>无权访问</h1>
              <ErrorState
                error={new Error("当前账户没有此管理功能的访问权限。")}
              />
            </>
          ) : (
            pages[route.page] || (
              <Empty
                title="页面不存在"
                action={<a href="#/chat">返回智能客服</a>}
              />
            )
          )}
        </Suspense>
      </ErrorBoundary>
    </AppLayout>
  );
}
