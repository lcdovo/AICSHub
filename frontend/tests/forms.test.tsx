import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { ApiError } from "../src/api/client";
import { CreateTicket } from "../src/features/tickets/TicketForms";
import { Dialog } from "../src/components/ui";
import LoginPage from "../src/pages/LoginPage";
function wrap(children: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>,
  );
}
describe("业务表单与可访问弹窗", () => {
  it("工单失败保留描述，重试沿用同一幂等键，阻止重复提交", async () => {
    const mock = vi
      .spyOn(api, "createTicket")
      .mockRejectedValueOnce(new Error("暂时失败"))
      .mockResolvedValueOnce(
        {} as Awaited<ReturnType<typeof api.createTicket>>,
      );
    const close = vi.fn();
    wrap(<CreateTicket userId={101} onClose={close} />);
    await userEvent.type(screen.getByLabelText("问题描述"), "合成问题描述");
    await userEvent.click(screen.getByRole("button", { name: "确认创建工单" }));
    expect(await screen.findByText("暂时失败")).toBeInTheDocument();
    expect(screen.getByLabelText("问题描述")).toHaveValue("合成问题描述");
    const firstKey = mock.mock.calls[0][1];
    await userEvent.click(screen.getByRole("button", { name: "确认创建工单" }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(mock.mock.calls[1][1]).toBe(firstKey);
  });
  it("工单失败后改参数生成新幂等键", async () => {
    const mock = vi.spyOn(api, "createTicket")
      .mockRejectedValueOnce(new Error("暂时失败"))
      .mockResolvedValueOnce({} as Awaited<ReturnType<typeof api.createTicket>>);
    wrap(<CreateTicket userId={101} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("问题描述"), { target: { value: "原始描述" } });
    fireEvent.click(screen.getByRole("button", { name: "确认创建工单" }));
    expect(await screen.findByText("暂时失败")).toBeInTheDocument();
    const firstKey = mock.mock.calls[0][1];
    fireEvent.change(screen.getByLabelText("问题描述"), { target: { value: "更新描述" } });
    fireEvent.click(screen.getByRole("button", { name: "确认创建工单" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    expect(mock.mock.calls[1][1]).not.toBe(firstKey);
    expect(mock.mock.calls[1][0].content).toBe("更新描述");
  });
  it("提交期间双击不创建重复工单", async () => {
    vi.spyOn(api, "createTicket").mockImplementation(
      () => new Promise(() => {}),
    );
    wrap(<CreateTicket userId={101} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("问题描述"), {
      target: { value: "验收问题" },
    });
    const button = screen.getByRole("button", { name: "确认创建工单" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.createTicket).toHaveBeenCalledOnce();
    expect(button).toBeDisabled();
  });
  it("弹窗有可访问标题，忙时禁止 Escape 关闭", () => {
    const close = vi.fn();
    const { rerender } = render(
      <Dialog title="确认操作" onClose={close} busy>
        内容
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "确认操作" });
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(close).not.toHaveBeenCalled();
    rerender(
      <Dialog title="确认操作" onClose={close}>
        内容
      </Dialog>,
    );
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(close).toHaveBeenCalledOnce();
  });
  it("登录错误靠近表单，重复提交被锁定", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new Error("凭据错误"));
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("用户名"), "qa_customer");
    await userEvent.type(screen.getByLabelText("密码"), "bad-password");
    await userEvent.click(
      screen.getByRole("button", { name: "登录", exact: true }),
    );
    expect(await screen.findByText("凭据错误")).toBeInTheDocument();
    expect(screen.getByLabelText("用户名")).toHaveValue("qa_customer");
    expect(screen.queryByText(/本机演示账户/)).not.toBeInTheDocument();
  });
  it("登录 401 显示凭据错误，而非登录过期", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError("后端拒绝凭据", 401));
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("用户名"), "qa_customer");
    await userEvent.type(screen.getByLabelText("密码"), "wrong-password");
    await userEvent.click(
      screen.getByRole("button", { name: "登录", exact: true }),
    );
    expect(
      await screen.findByText("用户名或密码错误，请检查后重试。"),
    ).toBeInTheDocument();
  });
});
