import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw } from "lucide-react";
import { api } from "../api";
import type { User } from "../api/types";
import {
  Button,
  Dialog,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  Pagination,
} from "../components/ui";
import { useTask } from "../hooks/useTask";
import { UserForm } from "../features/users/UserForm";
function DisableUser({ user, onClose }: { user: User; onClose: () => void }) {
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog title="停用用户账户" onClose={onClose} busy={task.pending}>
      <p>
        确认停用 {user.username}？该账户现有登录将失效，并无法继续访问客服。
      </p>
      <ErrorState error={task.error} />
      <div className="form-actions">
        <Button onClick={onClose} disabled={task.pending}>
          取消
        </Button>
        <Button
          tone="danger"
          busy={task.pending}
          onClick={() =>
            task.run(async () => {
              await api.disableUser(user.id);
              await cache.invalidateQueries({ queryKey: ["users"] });
              onClose();
            })
          }
        >
          确认停用
        </Button>
      </div>
    </Dialog>
  );
}
export default function UsersPage() {
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<User | "new">();
  const [disable, setDisable] = useState<User>();
  const users = useQuery({
    queryKey: ["users", page],
    queryFn: ({ signal }) => api.users(page, signal),
  });
  return (
    <>
      <PageHeader
        title="用户管理"
        description="管理账户资料、业务等级与访问状态。角色与权限由服务端控制。"
        actions={
          <>
            <Button
              busy={users.isFetching}
              onClick={() => void users.refetch()}
            >
              <RefreshCw size={16} />
              刷新
            </Button>
            <Button tone="primary" onClick={() => setEditor("new")}>
              <Plus size={16} />
              创建用户
            </Button>
          </>
        }
      />
      <ErrorState error={users.error} retry={() => void users.refetch()} />
      {users.isLoading ? (
        <Loading />
      ) : users.data?.length ? (
        <div className="data-table">
          <table>
            <thead>
              <tr>
                <th>账户</th>
                <th>角色</th>
                <th>业务等级</th>
                <th>访问状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id}>
                  <td data-label="账户">
                    <strong>{user.nickname || user.username}</strong>
                    <small>
                      {user.username} · #{user.id}
                    </small>
                  </td>
                  <td data-label="角色">
                    {user.role === "admin" ? "管理员" : "普通用户"}
                  </td>
                  <td data-label="业务等级">
                    {{ normal: "普通", vip: "VIP", enterprise: "企业" }[
                      user.level
                    ] || user.level}
                  </td>
                  <td data-label="访问状态">
                    <span
                      className={
                        "status " + (user.status ? "neutral" : "negative")
                      }
                    >
                      {user.status ? "正常" : "已停用"}
                    </span>
                  </td>
                  <td className="row-action">
                    <div className="actions">
                      <Button tone="plain" onClick={() => setEditor(user)}>
                        编辑
                      </Button>
                      {user.role !== "admin" && user.status && (
                        <Button tone="danger" onClick={() => setDisable(user)}>
                          停用
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        users.data && <Empty title="本页没有用户" />
      )}
      <Pagination
        page={page}
        hasNext={users.data?.length === 50}
        onChange={setPage}
      />
      {editor && (
        <UserForm
          user={editor === "new" ? undefined : editor}
          onClose={() => setEditor(undefined)}
        />
      )}
      {disable && (
        <DisableUser user={disable} onClose={() => setDisable(undefined)} />
      )}
    </>
  );
}
