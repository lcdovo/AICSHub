import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { User } from "../../api/types";
import { Button, Dialog, ErrorState, Field } from "../../components/ui";
import { useTask } from "../../hooks/useTask";
export function UserForm({
  user,
  onClose,
}: {
  user?: User;
  onClose: () => void;
}) {
  const [username, setUsername] = useState("");
  const [nickname, setNickname] = useState(user?.nickname || "");
  const [password, setPassword] = useState("");
  const [level, setLevel] = useState(user?.level || "normal");
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog
      title={user ? "编辑用户资料" : "创建普通用户"}
      onClose={onClose}
      busy={task.pending}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void task.run(async () => {
            if (user) await api.updateUser(user.id, { nickname, level });
            else
              await api.createUser({
                username: username.trim(),
                password,
                nickname,
                level,
              });
            await cache.invalidateQueries({ queryKey: ["users"] });
            onClose();
          });
        }}
      >
        {!user && (
          <Field label="用户名" hint="至少 3 个字符。">
            <input
              required
              minLength={3}
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>
        )}
        <Field label="昵称">
          <input
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
          />
        </Field>
        {!user && (
          <Field label="初始密码" hint="至少 8 个字符。">
            <input
              required
              minLength={8}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
        )}
        <Field label="业务等级">
          <select value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="normal">普通</option>
            <option value="vip">VIP</option>
            <option value="enterprise">企业</option>
          </select>
        </Field>
        <p className="muted">
          业务等级不会授予管理员权限；账户角色由服务端管理。
        </p>
        <ErrorState error={task.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={task.pending}>
            取消
          </Button>
          <Button type="submit" tone="primary" busy={task.pending}>
            {user ? "保存资料" : "确认创建用户"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
