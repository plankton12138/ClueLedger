/** 只用于控件状态与键盘验收；不作为产品页面或演示入口发布。 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Button } from "../../src/components/ui/Button";
import { Field } from "../../src/components/ui/Field";
import { ListItem } from "../../src/components/ui/ListItem";
import { Tabs } from "../../src/components/ui/Tabs";
import { AppDialog } from "../../src/components/ui/AppDialog";
import { StatusMessage, EmptyState } from "../../src/components/ui/StatusMessage";
function Fixture() {
  const [tab, setTab] = useState("first"), [selected, setSelected] = useState(false), [dialog, setDialog] = useState(false);
  return <main className="p-5 space-y-4" style={{ maxWidth: 1000 }}>
    <div className="flex flex-wrap gap-3">
      <Button variant="primary">保存</Button><Button>预览</Button><Button variant="quiet">详情</Button><Button variant="danger">删除</Button>
      <Button disabled disabledReason="先选择条目">发布</Button><Button busy>处理中</Button>
    </div>
    <p>发布前先选择条目。</p>
    <Field label="名称" error="[NAME_REQUIRED] 名称：内容为空。建议：填写名称。"><input defaultValue="" /></Field>
    <ListItem selected={selected} summary="摘要随当前行状态显示" onClick={() => setSelected(value => !value)}>条目</ListItem>
    <Tabs label="条目内容" value={tab} onValueChange={setTab} items={[{ value: "first", label: "正文", content: "正文内容" }, { value: "second", label: "历史", content: "历史内容" }]} />
    <StatusMessage tone="success">保存完成</StatusMessage><StatusMessage tone="warning">依据已改动</StatusMessage>
    <EmptyState>没有匹配的内容</EmptyState>
    <AppDialog title="编辑条目" open={dialog} onOpenChange={setDialog} trigger={<Button>打开编辑</Button>}>
      <Field label="条目标题"><input defaultValue="雨夜" /></Field><Button>保存修改</Button>
    </AppDialog>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
