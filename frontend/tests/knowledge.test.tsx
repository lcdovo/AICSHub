import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { DocumentEditor } from "../src/features/knowledge/DocumentEditor";
import type { KnowledgeDocument } from "../src/api/types";

describe("知识版本编辑",()=>{
  it("提交打开时的版本，冲突后保留正文供用户处理",async()=>{
    const update=vi.spyOn(api,"updateDocument").mockRejectedValue(new Error("document_version_conflict"));
    const doc:KnowledgeDocument={id:"kb-synthetic",title:"合成政策",category:"policy",keywords:[],source:"synthetic",status:"published",version:3,published_version:3,chunks_count:1};
    const close=vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><DocumentEditor document={doc} onClose={close}/></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("替换正文（可选）"),{target:{value:"新的合成正文"}});
    fireEvent.click(screen.getByRole("button",{name:"确认更新版本"}));
    expect(await screen.findByText("document_version_conflict")).toBeInTheDocument();
    expect(update).toHaveBeenCalledWith("kb-synthetic",expect.objectContaining({expected_version:3,content:"新的合成正文"}));
    expect(screen.getByLabelText("替换正文（可选）")).toHaveValue("新的合成正文");
    expect(close).not.toHaveBeenCalled();
  });
  it("新增文档明确排队索引，保存不展示立即发布承诺",()=>{
    render(<QueryClientProvider client={new QueryClient()}><DocumentEditor onClose={()=>{}}/></QueryClientProvider>);
    expect(screen.getByText(/保存后排队索引，验证成功才发布/)).toBeInTheDocument();
    expect(screen.getByRole("button",{name:"保存并排队索引"})).toBeDisabled();
  });
});
