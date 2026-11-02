import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../state/auth";
import { ErrorState, PageHeader, Status } from "../components/ui";
export default function AccountPage() {
  const user = useAuth((s) => s.user)!;
  const account = useQuery({
    queryKey: ["account", user.id],
    queryFn: ({ signal }) => api.me(user.id, signal),
  });
  const current = account.data || user;
  return (
    <>
      <PageHeader
        title={user.role === "admin" ? "管理员账户" : "个人账户"}
        description="查看当前登录身份与账户状态。"
      />
      <ErrorState error={account.error} retry={() => void account.refetch()} />
      <section className="account-section">
        <h2>{current.nickname || current.username}</h2>
        <dl className="details-list">
          <dt>用户名</dt>
          <dd>{current.username}</dd>
          <dt>账户角色</dt>
          <dd>{current.role === "admin" ? "管理员" : "普通用户"}</dd>
          <dt>业务等级</dt>
          <dd>
            {{ normal: "普通", vip: "VIP", enterprise: "企业" }[
              current.level
            ] || current.level}
          </dd>
          <dt>账户状态</dt>
          <dd>
            <Status value={current.status ? "active" : "disabled"} />
          </dd>
          <dt>登录状态</dt>
          <dd>已登录</dd>
        </dl>
        <p className="muted">如需退出，使用导航中的“退出登录”。</p>
      </section>
    </>
  );
}
