import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import * as client from "../src/api/client";
import { RefundPanel } from "../src/features/chat/RefundPanel";

const action = { action_id: "synthetic-action", version: 3, order_id: "ORD900001", type: "refund",
  reason: "合成商品破损", amount: "0.10", currency: "CNY", expires_at: "2099-01-01T00:00:00", status: "pending" };
function wrap(admin = false, confirm = vi.fn().mockResolvedValue(undefined)) {
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
    <RefundPanel sessionId="synthetic-session" busy={false} admin={admin} confirm={confirm}/>
  </QueryClientProvider>);
  return confirm;
}
describe("模拟退款明确动作", () => {
  it("展示精确金额并确认所选动作版本，取消使用独立入口", async () => {
    const request = vi.spyOn(client, "request").mockImplementation(async path =>
      path.includes("/pending?") ? [action] : []);
    const confirm = wrap();
    expect(await screen.findByText(/0.10 CNY/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"确认此模拟申请"}));
    await waitFor(()=>expect(confirm).toHaveBeenCalledWith("synthetic-action",3));
    await waitFor(()=>expect(screen.getByRole("button",{name:"取消此预览"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name:"取消此预览"}));
    await waitFor(()=>expect(request).toHaveBeenCalledWith("/api/v1/refunds/actions/synthetic-action/cancel",expect.objectContaining({method:"POST"})));
  });
  it("管理员不能替客户确认，审核必须填写意见并明确不支付", async () => {
    const request=vi.spyOn(client,"request").mockImplementation(async path=> path.includes("/pending?")?[action]:[
      {refund_id:"synthetic-refund",order_id:"ORD900001",amount:"0.10",currency:"CNY",status:"pending_review",status_label:"模拟申请待审核",is_simulated:true}]);
    const confirm=wrap(true);
    expect(await screen.findByRole("button",{name:"确认此模拟申请"})).toBeDisabled();
    const approve=await screen.findByRole("button",{name:"模拟审核通过（不支付）"});
    expect(approve).toBeDisabled();
    fireEvent.change(screen.getByLabelText("模拟审核意见"),{target:{value:"合成审核意见"}});
    fireEvent.click(approve);
    await waitFor(()=>expect(request).toHaveBeenCalledWith("/api/v1/refunds/synthetic-refund/review",expect.objectContaining({body:JSON.stringify({decision:"approved",note:"合成审核意见"})})));
    expect(confirm).not.toHaveBeenCalled();
  });
});
