import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { MessageList } from "../src/features/chat/MessageList";
import FeedbackPage from "../src/pages/FeedbackPage";

function wrap(element: React.ReactNode) {
  HTMLElement.prototype.scrollTo = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
  return client;
}
describe("回答反馈", () => {
  it("空列表引导切换全部反馈", async () => {
    vi.spyOn(api, "feedbackRecords").mockResolvedValue({
      total: 0,
      records: [],
    });
    wrap(<FeedbackPage />);
    fireEvent.click(screen.getByRole("button", { name: "点踩", exact: true }));
    expect(await screen.findByText("暂无点踩反馈")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看全部反馈" }));
    expect(await screen.findByText("还没有收到反馈")).toBeInTheDocument();
  });
  it("长回复支持展开与收起", async () => {
    vi.spyOn(api, "feedbackRecords").mockResolvedValue({
      total: 1,
      records: [
        {
          feedback_id: "long",
          username: "合成顾客",
          session_id: "demo",
          feedback_type: "like",
          answer: "合成客服回复。".repeat(40),
          created_at: "2026-11-11T01:00:00",
        },
      ],
    });
    wrap(<FeedbackPage />);
    const expand = await screen.findByRole("button", { name: "展开完整回复" });
    fireEvent.click(expand);
    expect(screen.getByRole("button", { name: "收起原文" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "收起原文" }));
    expect(
      screen.getByRole("button", { name: "展开完整回复" }),
    ).toHaveAttribute("aria-expanded", "false");
  });
  it("恢复评价并允许改为点踩", async () => {
    vi.spyOn(api, "sessionVotes").mockResolvedValue({ "42": "like" });
    const submit = vi.spyOn(api, "feedback").mockResolvedValue({});
    wrap(
      <MessageList
        userId={1}
        sessionId="test"
        feedback
        messages={[{ id: 42, role: "assistant", content: "合成回答" }]}
      />,
    );
    expect(
      await screen.findByRole("button", { name: "已点赞" }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "点踩" }));
    expect(
      await screen.findByRole("button", { name: "已点踩" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("评价已保存");
    expect(submit).toHaveBeenCalledWith(
      1,
      "test",
      expect.objectContaining({ id: 42 }),
      "dislike",
    );
  });
  it("后台展示原文并按点踩筛选", async () => {
    const records = vi.spyOn(api, "feedbackRecords").mockResolvedValue({
      total: 1,
      records: [
        {
          feedback_id: "feedback",
          username: "synthetic",
          session_id: "test",
          feedback_type: "dislike",
          answer: "待改进的合成回答",
          created_at: "2026-11-11T01:00:00",
        },
      ],
    });
    wrap(<FeedbackPage />);
    expect(await screen.findByText("待改进的合成回答")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "dislike" },
    });
    await waitFor(() =>
      expect(records).toHaveBeenLastCalledWith(
        1,
        "dislike",
        expect.any(AbortSignal),
      ),
    );
  });
});
