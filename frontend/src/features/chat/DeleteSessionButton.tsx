import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api } from "../../api";
import type { Session } from "../../api/types";
import { Button, Dialog, ErrorState } from "../../components/ui";
import { useTask } from "../../hooks/useTask";
import { useChat } from "../../state/chat";
import { isBusy } from "./protocol";

export function DeleteSessionButton({ session, onDeleted }: {
  session: Session;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const task = useTask();
  const cache = useQueryClient();
  const phase = useChat((state) => state.rooms[session.id]?.phase);
  const busy = isBusy(phase || "idle") || phase === "complete";
  const title = session.title || "客服会话";

  return <>
    <Button tone="plain" className="session-delete"
      aria-label={"删除会话：" + title}
      title={busy ? "请等待本轮回复保存后再删除" : "删除会话"}
      disabled={busy || task.pending} onClick={() => setOpen(true)}>
      <Trash2 size={15} aria-hidden="true" />
    </Button>
    {open && <Dialog title="删除会话" busy={task.pending} onClose={() => setOpen(false)}>
      <p>确定删除“{title}”吗？删除后将无法在会话列表中查看或继续此会话，此操作无法撤销。</p>
      <ErrorState error={task.error} />
      <div className="form-actions">
        <Button disabled={task.pending} onClick={() => setOpen(false)}>取消</Button>
        <Button tone="danger" busy={task.pending} disabled={busy}
          onClick={() => task.run(async () => {
            await api.deleteSession(session.id);
            await cache.cancelQueries({ queryKey: ["session", session.id] });
            await cache.cancelQueries({ queryKey: ["history", session.id] });
            useChat.getState().removeSession(session.id);
            sessionStorage.removeItem("aicshub-pending-run:" + session.id);
            sessionStorage.removeItem("aicshub-pending-client:" + session.id);
            setOpen(false);
            onDeleted();
            cache.removeQueries({ queryKey: ["session", session.id] });
            cache.removeQueries({ queryKey: ["history", session.id] });
            await cache.invalidateQueries({ queryKey: ["sessions"] });
          })}>确认删除</Button>
      </div>
    </Dialog>}
  </>;
}
